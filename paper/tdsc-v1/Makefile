LATEXMK ?= latexmk
TEX := manuscript/tdsc.tex
PDF := manuscript/tdsc.pdf

.PHONY: all clean

all: $(PDF)

$(PDF): $(TEX) manuscript/references.bib
	cd manuscript && $(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error tdsc.tex

clean:
	cd manuscript && $(LATEXMK) -C tdsc.tex

