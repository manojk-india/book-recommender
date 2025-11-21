import os
import javalang

def build_index(project_root):
    """Map class name → file path for all .java files in project."""
    index = {}
    for root, _, files in os.walk(project_root):
        for f in files:
            if f.endswith(".java"):
                class_name = f.replace(".java", "")
                index[class_name] = os.path.join(root, f)
    return index


def parse_file(filepath):
    """Parse a Java file and extract class references using javalang AST."""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    try:
        tree = javalang.parse.parse(content)
    except:
        return set()

    refs = set()

    # 1. Imports
    for imp in tree.imports:
        name = imp.path.split(".")[-1]
        refs.add(name)

    # 2. Class declaration (extends, implements)
    for _, node in tree.filter(javalang.tree.ClassDeclaration):
        if node.extends:
            refs.add(node.extends.name)
        if node.implements:
            for impl in node.implements:
                refs.add(impl.name)

    # 3. Object creations: new ClassName()
    for _, node in tree.filter(javalang.tree.ClassCreator):
        refs.add(node.type.name)

    # 4. Static calls: ClassName.method()
    for _, node in tree.filter(javalang.tree.MethodInvocation):
        if node.qualifier:
            refs.add(node.qualifier)

    # 5. Field types: ClassName fieldName;
    for _, node in tree.filter(javalang.tree.FieldDeclaration):
        refs.add(node.type.name)

    # 6. Method parameter types
    for _, node in tree.filter(javalang.tree.MethodDeclaration):
        if node.parameters:
            for param in node.parameters:
                refs.add(param.type.name)

    # Filter out Java keywords and primitives
    banned = {"int", "long", "double", "float", "char", "String", 
              "boolean", "void", "var"}
    refs = {r for r in refs if r not in banned}

    return refs


def get_dependencies(target_file, project_root):
    """Return all recursive project dependencies for given file."""
    index = build_index(project_root)
    visited = set()
    result = set()

    def dfs(path):
        if path in visited:
            return
        visited.add(path)

        refs = parse_file(path)

        for r in refs:
            if r in index:
                dep_path = index[r]
                result.add(dep_path)
                dfs(dep_path)

    dfs(target_file)
    return result
