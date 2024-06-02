build/export.json:
	@mkdir -p build
	python3 ../py/export.py json "$@" build/export.json.d
-include build/export.json.d
clean:
	rm -rf build
