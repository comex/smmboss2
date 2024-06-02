build/export.json:
	@mkdir -p build
	python3 ../py/export.py "$@" build/export.json.d
-include build/export.json.d
clean:
	rm -rf build
