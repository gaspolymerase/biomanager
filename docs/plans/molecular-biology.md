# Molecular biology: the round, as built

The plasmid page draws and edits the plasmid itself. Everything the round
set out to do is in:

| What | Where |
| --- | --- |
| Maps, every base and its protein, open reading frames | `static/plasmid-map.js`, `static/seq-viewer.js`, `app/protein.py` |
| Virtual digests and gels, with Dam/Dcm | `app/digest.py` |
| The lab's primers that bind, and a PCR | `app/primer_design.py`, `app/sequence_routes.py` |
| Clones checked by sequencing, and accepted | `app/seqverify.py`, `app/clone_routes.py` |
| Site-directed mutagenesis, and silent site changes | `app/mutagenesis.py` |
| Digest and ligate, Gibson, In-Fusion, Golden Gate | `app/cloning.py` |
| Gateway BP and LR | `app/gateway.py` |
| Compare two plasmids | `app/plasmid_compare.py` |
| A sequence by its Addgene number or GenBank accession | `app/sequence_fetch.py` |
| Editing the bases, the features and the topology | `app/sequence_edit.py` |

With the last of those the app draws and edits a plasmid on its own:
Open Vector Editor is gone, and with it some 9 MB of JavaScript and CSS
that every plasmid page used to load.

## Left out on purpose

- **Multiple sequence alignment** of many sequences or proteins: rare at
  the bench, and well served by tools made for it.
- **TA and TOPO cloning simulation**: the product is the insert in a known
  vector at a known place; a lab records it with Made from.
- **A secondary-structure viewer for primers**: the hairpin and self-dimer
  ΔG the primer panel gives answer the question a lab asks.
- **Multi-site Gateway** (att3, att4, att5): one pair of sites a reaction
  is what a lab does; the rest is a second plasmid's worth of machinery.
- **A rearrangement-scale compare**: two plasmids that differ by more than
  the read aligner's band show as the aligned stretch ending early, which
  says enough for the near-identical constructs the feature is for.
