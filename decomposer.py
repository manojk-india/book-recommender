#!/usr/bin/env python3
"""
dep_extractor.py

Usage:
    python dep_extractor.py /path/to/java/project --out results.json

Output:
 - <out> will contain direct dependency graph and per-class metadata.
 - results_transitive.json will contain transitive closure for each class.

Notes:
 - Uses javalang for AST parsing + regex heuristics for legacy patterns.
 - Improve heuristics for your codebase if needed.
"""

import os
import re
import json
import argparse
from collections import defaultdict, deque

try:
    import javalang
except Exception as e:
    raise SystemExit("Missing dependency: pip install javalang") from e

# -----------------------
# Regex heuristics
# -----------------------
RE_NEW = re.compile(r'\bnew\s+([A-Za-z_][A-Za-z0-9_]*(?:<[^\>]+>)?)')  # new ClassName or new ClassName<...>
RE_FQCN = re.compile(r'\b([a-z][\w]*(?:\.[A-Za-z_][\w]*)+\.[A-Z][A-Za-z0-9_]+)\b')  # com.x.YClass
RE_SIMPLE_FQ = re.compile(r'\b([A-Z][A-Za-z0-9_]+)\b')  # simple identifiers starting with uppercase (class candidates)
RE_STATIC_CALL = re.compile(r'\b([A-Z][A-Za-z0-9_]+)\s*\.\s*[A-Za-z_][A-Za-z0-9_]*\s*\(')  # ClassName.method(
RE_MEMBER_REF = re.compile(r'\b([A-Z][A-Za-z0-9_]+)\s*\.\s*[A-Za-z_][A-Za-z0-9_]*\b')  # ClassName.something

# -----------------------
# Helpers
# -----------------------
def list_java_files(root):
    files = []
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith('.java'):
                files.append(os.path.join(dirpath, f))
    return files

def read_file(path):
    with open(path, 'r', encoding='utf-8', errors='ignore') as fh:
        return fh.read()

def simple_class_name(fqcn):
    return fqcn.split('.')[-1]

# -----------------------
# Extractors
# -----------------------
def parse_with_javalang(src):
    try:
        tree = javalang.parse.parse(src)
        return tree
    except Exception:
        return None

def extract_from_ast(tree):
    """
    Extract candidate dependencies using javalang AST where possible.
    Returns a dict with keys: package, types(list of class names), imports, extends, implements,
    method_invocations (qualifier based), member_references (qualifier based), creators (new expressions).
    """
    result = {
        'package': None,
        'types': [],  # top-level types defined in file (class/interface)
        'imports': [],
        'extends': set(),
        'implements': set(),
        'method_invocations': set(),
        'member_references': set(),
        'creators': set(),  # object creation via new
    }

    if tree is None:
        return result

    # package
    if getattr(tree, 'package', None) is not None:
        result['package'] = tree.package.name

    # imports
    for imp in getattr(tree, 'imports', []) or []:
        result['imports'].append(imp.path)

    # types (top-level)
    for t in getattr(tree, 'types', []) or []:
        # t is e.g. ClassDeclaration
        if getattr(t, 'name', None):
            result['types'].append(t.name)
        # extends / implements
        if getattr(t, 'extends', None):
            # t.extends can be ReferenceType or list
            ext = t.extends
            if isinstance(ext, list):
                for e in ext:
                    if getattr(e, 'name', None):
                        result['extends'].add(e.name)
            else:
                if getattr(ext, 'name', None):
                    result['extends'].add(ext.name)
        if getattr(t, 'implements', None):
            for impl in t.implements:
                if getattr(impl, 'name', None):
                    result['implements'].add(impl.name)

    # walk nodes for MethodInvocation, MemberReference, and ClassCreator-like patterns
    try:
        for path, node in tree.filter(javalang.tree.MethodInvocation):
            # qualifier may be None or a variable/class name
            qual = getattr(node, 'qualifier', None)
            if qual:
                result['method_invocations'].add(qual)
            # sometimes node.selectors or member present; we collect member names too if needed

        for path, node in tree.filter(javalang.tree.MemberReference):
            qual = getattr(node, 'qualifier', None)
            if qual:
                result['member_references'].add(qual)

        # javalang uses 'ClassCreator' nodes for 'new' expressions in some versions,
        # but to be safe, we'll also use regex on source to capture 'new' usage.
        for path, node in tree.filter(javalang.tree.ClassCreator):
            t = getattr(node, 'type', None)
            if getattr(t, 'name', None):
                result['creators'].add(t.name)
    except Exception:
        # Some javalang versions/trees may not have these node types or raise during filter.
        pass

    return {k: (list(v) if isinstance(v, set) else v) for k, v in result.items()}

def extract_with_regex(src):
    """
    Heuristic-based extraction using regex for:
    - new ClassName
    - fully-qualified class names (com.example.Foo)
    - static calls ClassName.method(...)
    """
    res = {
        'new': set(),
        'fqcn': set(),
        'static_classes': set(),
        'simple_identifiers': set(),
    }

    for m in RE_NEW.finditer(src):
        name = m.group(1).split('<', 1)[0].strip()
        # skip primitives and arrays
        if name and not name[0].islower():
            res['new'].add(name)

    for m in RE_FQCN.finditer(src):
        fq = m.group(1)
        res['fqcn'].add(fq)
        res['simple_identifiers'].add(simple_class_name(fq))

    for m in RE_STATIC_CALL.finditer(src):
        cls = m.group(1)
        res['static_classes'].add(cls)
        res['simple_identifiers'].add(cls)

    # poor man's class guesses: capitalized identifiers (could be many false positives)
    for m in RE_SIMPLE_FQ.finditer(src):
        ident = m.group(1)
        # filter common Java keywords and types to reduce noise
        if ident not in {'String','Integer','Long','Boolean','Double','Float','List','Map','Set','Optional'}:
            res['simple_identifiers'].add(ident)

    return {k: list(v) for k, v in res.items()}

# -----------------------
# Main scan per file
# -----------------------
def analyze_file(path, all_package_classes_map):
    """
    Analyze a single java file and return a metadata dict:
    {
      'file_path':..., 'package':..., 'class_names': [...],
      'imports': [...], 'extends': [...], 'implements': [...],
      'new': [...], 'static_calls': [...], 'fqcn': [...], 'same_package_candidates': [...]
    }
    """
    src = read_file(path)
    tree = parse_with_javalang(src)
    ast_info = extract_from_ast(tree)
    regex_info = extract_with_regex(src)

    package = ast_info.get('package') or None
    class_names = ast_info.get('types') or []

    imports = ast_info.get('imports') or []
    extends = ast_info.get('extends') or []
    implements = ast_info.get('implements') or []

    # Collect direct deps
    deps = set()

    # 1) Imports: add simple names and FQCNs
    for imp in imports:
        deps.add(imp)
        deps.add(simple_class_name(imp))

    # 2) AST creators (new)
    for c in ast_info.get('creators', []) or []:
        deps.add(c)

    # 3) regex new
    for c in regex_info.get('new', []):
        deps.add(c)

    # 4) method invocation qualifiers and member references (qualifier might be class or var)
    for q in ast_info.get('method_invocations', []) or []:
        # heuristics: if qualifier starts with uppercase, it's likely a type/class
        if q and q[0].isupper():
            deps.add(q)
    for q in ast_info.get('member_references', []) or []:
        if q and q[0].isupper():
            deps.add(q)

    # 5) static class heuristics
    for c in regex_info.get('static_classes', []):
        deps.add(c)

    # 6) fully-qualified names from regex
    for fq in regex_info.get('fqcn', []):
        deps.add(fq)
        deps.add(simple_class_name(fq))

    # 7) includes class names in same package (detect usage by simple name)
    same_pkg_candidates = set()
    if package and package in all_package_classes_map:
        # any class in the same package referenced by simple name in this file
        # check if the simple name appears in source (word boundary)
        for class_in_pkg in all_package_classes_map[package]:
            # simple check, could be improved by AST type references
            if re.search(r'\b' + re.escape(class_in_pkg) + r'\b', src):
                same_pkg_candidates.add(class_in_pkg)
                deps.add(class_in_pkg)

    # 8) extends/implements
    for e in extends:
        deps.add(e)
    for i in implements:
        deps.add(i)

    # heuristics: add capitalized simple identifiers (careful: noisy)
    # already collected in regex_info['simple_identifiers'] - add cautiously
    for s in regex_info.get('simple_identifiers', []):
        # avoid adding if looks like local variable or java.lang types
        if len(s) > 1 and s[0].isupper():
            # if it's already known via imports or same-package, keep; else add as candidate
            deps.add(s)

    metadata = {
        'file_path': path,
        'package': package,
        'class_names': class_names,
        'imports': imports,
        'extends': list(extends),
        'implements': list(implements),
        'new': list(set(ast_info.get('creators', [])) | set(regex_info.get('new', []))),
        'static_calls': list(regex_info.get('static_classes', [])),
        'fqcn': list(regex_info.get('fqcn', [])),
        'same_package_candidates': list(same_pkg_candidates),
        'direct_dependencies': sorted([d for d in deps if d]),
    }

    return metadata

# -----------------------
# Build project-wide maps
# -----------------------
def build_package_class_map(java_files):
    """
    Build mapping: package -> set(class names found in files in that package)
    """
    pkg_map = defaultdict(set)
    for p in java_files:
        src = read_file(p)
        tree = parse_with_javalang(src)
        package = None
        if tree and getattr(tree, 'package', None):
            package = tree.package.name
        # extract class names via AST and also via simple heuristic if AST fails
        class_names = []
        if tree and getattr(tree, 'types', None):
            for t in tree.types:
                if getattr(t, 'name', None):
                    class_names.append(t.name)
        else:
            # fallback: derive from filename (conservative)
            fname = os.path.splitext(os.path.basename(p))[0]
            class_names.append(fname)
        if package:
            for c in class_names:
                pkg_map[package].add(c)
    return {k: list(v) for k, v in pkg_map.items()}

# -----------------------
# Graph utilities
# -----------------------
def build_graph(metadata_list):
    """
    Build graph keyed by class_name (prefer including package for uniqueness).
    We'll use identifier keys as follows:
      - if file contains a single top-level type: package.ClassName
      - else fallback to filepath-based key

    Graph value is list of dependency identifiers (simple names and fqcn).
    """
    graph = {}
    meta_by_key = {}

    for m in metadata_list:
        pkg = m['package']
        class_names = m['class_names']
        if class_names:
            # if multiple top-level classes, create separate entries for each (rare)
            for cname in class_names:
                key = (pkg + "." + cname) if pkg else cname
                graph[key] = m['direct_dependencies']
                meta_by_key[key] = m
        else:
            # fallback to filename key
            fname = os.path.splitext(os.path.basename(m['file_path']))[0]
            key = (pkg + "." + fname) if pkg else fname
            graph[key] = m['direct_dependencies']
            meta_by_key[key] = m

    return graph, meta_by_key

def transitive_closure(graph):
    """
    Compute transitive closure for each node (class).
    Uses BFS/DFS, returns: node -> set(all reachable nodes)
    """
    closure = {}
    for node in graph:
        visited = set()
        stack = [node]
        while stack:
            cur = stack.pop()
            for dep in graph.get(cur, []):
                # dependency names in graph may be simple names or FQCN.
                # Try to match dependency to a graph node: prefer exact match, else suffix match
                matched_nodes = [n for n in graph if n == dep or n.endswith("." + dep) or n.split('.')[-1] == dep]
                for mn in matched_nodes:
                    if mn not in visited and mn != node:
                        visited.add(mn)
                        stack.append(mn)
        closure[node] = sorted(list(visited))
    return closure

# -----------------------
# Main
# -----------------------
def main(root, out_path):
    print(f"Scanning Java files under: {root}")
    java_files = list_java_files(root)
    print(f"Found {len(java_files)} .java files")

    pkg_map = build_package_class_map(java_files)
    print(f"Discovered {len(pkg_map)} packages")

    metadata_list = []
    for f in java_files:
        meta = analyze_file(f, pkg_map)
        metadata_list.append(meta)

    graph, meta_by_key = build_graph(metadata_list)
    print(f"Built graph with {len(graph)} nodes")

    closure = transitive_closure(graph)
    print("Computed transitive closure")

    out = {
        'graph': graph,
        'meta': meta_by_key,
    }
    with open(out_path, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, indent=2)

    trans_out_path = out_path.replace('.json', '_transitive.json')
    with open(trans_out_path, 'w', encoding='utf-8') as fh:
        json.dump(closure, fh, indent=2)

    print(f"Wrote direct graph to {out_path}")
    print(f"Wrote transitive closure to {trans_out_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract Java class dependency graph (direct+transitive)')
    parser.add_argument('root', help='root folder of Java project')
    parser.add_argument('--out', default='results.json', help='output JSON file')
    args = parser.parse_args()
    main(args.root, args.out)
