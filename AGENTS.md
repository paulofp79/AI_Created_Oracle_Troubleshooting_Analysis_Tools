# Runtime host note

The location of this source checkout is not necessarily the host where the
application runs. The application may be started on a different host. Do not
infer that the application is down because its process or listening port is
absent on this checkout's host. Check the configured runtime host when one is
provided; otherwise, report that runtime status cannot be determined from this
node.
