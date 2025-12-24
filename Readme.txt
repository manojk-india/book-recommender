Following your suggestion, we spent some time reviewing the JVM-based approach through documentation, reference material, and discussions with a few senior team members to better understand its applicability in our context.

From this exploration, one aspect that stood out is the distinction between JVM class loading and Spring’s bean initialization lifecycle. In our application’s current configuration, it appears that a majority of beans are eagerly initialized during startup due to component scanning and dependency injection. As a result, many classes are already loaded into the JVM before any specific API is invoked.

Because of this, observing the set of classes loaded at runtime when an API is triggered may not always reflect the minimal or API-specific dependency set, but rather the broader application context that was initialized upfront.

That said, we do see merit in the runtime-based perspective you suggested, and we believe it could be valuable as a complementary or validation mechanism alongside static analysis and that is the reason we are trying to configure AppMap as you suggested to trace calls 

Thank you for pointing us in this direction and for the continued feedback.
