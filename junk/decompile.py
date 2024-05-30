#!/usr/bin/env python3
import binaryninja, sys
with binaryninja.open_view(sys.argv[1]) as bv:
	for fn in bv.functions:
		source = '\n'.join(map(str, fn.hlil.root.lines))
		print(source)

