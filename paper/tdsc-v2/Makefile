LATEXMK ?= latexmk
TEX := sessionbound-tdsc.tex
PDF := sessionbound-tdsc.pdf

.PHONY: all clean

all: $(PDF)

$(PDF): $(TEX) references.bib IEEEtran.cls IEEEtran.bst
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error $(TEX)

clean:
	$(LATEXMK) -C $(TEX)

