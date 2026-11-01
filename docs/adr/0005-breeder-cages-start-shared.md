# A Breeder cage starts shared with the lab; other cages start personal

The lab's breeding stock is kept in parts by different people, and anyone
setting up a mating needs to take mice from it, so a cage whose purpose is
**Breeder** starts shared with the lab (`access.STARTS_SHARED_PURPOSES`), and
the Breeders tab lists these cages for anyone to **Pick** from. Any other
cage starts personal; its owner or an admin may share it. Sharing is a stored
flag, not read from the purpose each time: revision 0013 set it on the
breeder cages labs already had, and a cage that stops being a Breeder cage
starts personal. A **Breeding** cage (mating now) is its owner's.

## Consequences

- Who may edit a cage can differ from what its purpose suggests, because its
  owner may have made a Breeder cage personal or shared another one.
- The zebrafish breeder tank is the same idea and is shared by its purpose
  (ADR 0006).
