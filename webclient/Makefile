WEBCLIENT ?= .
WBUILD := $(abspath $(WEBCLIENT)/build)
$(WBUILD)/ran_tsc: $(glob $(WEBCLIENT)/*.ts) $(WBUILD)/mm_types.ts $(WEBCLIENT)/tsconfig.json Makefile
	tsc -p $(WEBCLIENT)/tsconfig.json && touch $@
$(WBUILD)/mm_types.ts: Makefile
	mkdir -p $(WBUILD)
	cd $(WEBCLIENT)/../py && uv run export.py typescript $@ $(WBUILD)/mm_types.ts.d
-include $(WBUILD)/mm_types.ts.d
.PHONY: tsc-watch
tsc-watch:
	tsc -p $(WEBCLIENT)/tsconfig.json -w
clean:
	rm -rf $(WEBCLIENT)/build
