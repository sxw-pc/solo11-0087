.PHONY: install demo test crawl export portal review

install:
	python -m pip install ".[test]"

demo:
	python -m tender_anchor demo --crawl

test:
	pytest -q

crawl:
	python -m tender_anchor crawl

export:
	python -m tender_anchor export

portal:
	python -m tender_anchor serve-portal

review:
	python -m tender_anchor serve-review
