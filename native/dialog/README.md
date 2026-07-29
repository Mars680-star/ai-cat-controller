# Dialogue demo

`volc_conv_ai_demo.c` is retained as a directly readable snapshot of the
modified dialogue entry point. It is not a standalone program: build it inside
the locked Volcengine SDK after applying the patches and overlay under
`integrations/volcengine-k1`.

The file contains the K1 audio path, service-mode wake signal, bounded capture,
disconnect handling and `shake_head` Function Calling implementation.
