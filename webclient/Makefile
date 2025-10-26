WEBCLIENT ?= .
WBUILD := $(abspath $(WEBCLIENT)/build)
$(WBUILD)/ran_tsc: $(glob $(WEBCLIENT)/*.ts) $(WBUILD)/export.ts
	tsc -p $(WEBCLIENT)/tsconfig.json && touch $@
$(WBUILD)/export.ts:
	mkdir -p $(WBUILD)
	cd $(WEBCLIENT)/../py && uv run export.py typescript $@ $(WBUILD)/export.ts.d
-include $(WBUILD)/export.ts.d
.PHONY: tsc-watch
tsc-watch:
	tsc -p $(WEBCLIENT)/tsconfig.json -w
clean:
	rm -rf $(WEBCLIENT)/build
