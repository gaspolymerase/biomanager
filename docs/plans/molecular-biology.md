# Molecular biology: the next round

The plasmid page now draws the plasmid itself (Overview, Sequence, Digest,
Verify, Primers): maps, every base and its protein, virtual digests and
gels with Dam/Dcm, the lab's primers that bind and a PCR, and clones checked
by sequencing and accepted. Compared with what SnapGene offers, these are
what a lab would still reach for, in the order they are worth doing.
Each builds on code that is already there.

## 1. Site-directed mutagenesis

Pick bases on the Sequence tab and say what they should become: a
substitution, an insertion or a deletion. The app designs the two primers,
back to back as NEB's Q5 kit has them (the change in the 5′ end of the
forward primer, or split for a large insertion), each annealing to Tm
targets, checked as any new primer is (`app/primer_design.py`), and shows
the product's sequence and what it does to the protein (`app/protein.py`).
**Make it** creates the new plasmid with Made from set to the template and
the method `mutagenesis`, its primers in the Primers database to order, as
an assembly's are (`app/cloning_routes.py`).

Also here: a silent change that adds or removes an enzyme site
(`app/digest.py` finds the sites; the codon table gives the choices), for
a diagnostic digest or to free a site for cloning.

## 2. In-Fusion and Gateway in Assemble a plasmid

- **In-Fusion** is Gibson's design with 15-base overlaps and the vector cut,
  not amplified: a variant of `cloning.design_gibson_primers` with a
  different overlap length and the vector as a digest fragment.
- **Gateway** BP (attB × attP → attL entry clone) and LR (attL × attR →
  attB expression clone): find the att sites by sequence (they belong in
  the feature library), recombine between matching pairs, keep the
  features, and say when a ccdB cassette is lost or kept. One pair of
  sites per reaction to start with.

## 3. Compare two plasmids

From a plasmid's Overview, **Compare with…** another plasmid (or a file
not yet in the lab): the two maps one over the other, aligned with the
same banded alignment `app/seqverify.py` uses for reads, every difference
listed with the features it falls in and what it does to a protein. The
use: which of two near-identical constructs is in a tube, and what a
colleague's "same plasmid" actually changed.

## 4. Fetch a sequence by its number

**New plasmid → From Addgene or NCBI**: an Addgene plasmid number or a
GenBank accession fills the sequence and features. Optional, and off when
the lab's server has no internet (the Feature library's pack download
shows how to ask and fail politely). NCBI's E-utilities need no key for a
lab's volume; Addgene's full sequences come from its public GenBank files.

## 5. Editing in the app's own viewer

The Sequence tab already picks bases. Typing over a selection, inserting
and deleting bases, adding a feature or a primer from a selection, and
undo would let the Open Vector Editor bundle go: features that a change
runs through are shifted or cut as `cloning.carry_features` already does
for fragments, every save is a version (`app/plasmid_versions.py`), and
the editor's JavaScript (some 3 MB) stops loading on every plasmid page.

## Left out on purpose

- **Multiple sequence alignment** of many sequences or proteins: rare at
  the bench, and well served by tools made for it.
- **TA and TOPO cloning simulation**: the product is the insert in a known
  vector at a known place; a lab records it with Made from.
- **A secondary-structure viewer for primers**: the hairpin and self-dimer
  ΔG the primer panel gives answer the question a lab asks.
