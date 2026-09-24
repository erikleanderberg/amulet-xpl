# CAD

Curated enclosure models live here — the ones someone would actually print, not every iteration.

Suggested layout, one folder per released revision:

```
cad/
└── v48-wrist/
    ├── README.md            what changed, and why this revision exists
    ├── 01-base.step         neutral interchange format, always include
    ├── 02-lid.step
    ├── 01-base.stl          ready to slice
    ├── 02-lid.stl
    ├── source.f3d           parametric source
    └── render.png           one picture, so the folder is browsable
```

**Keep iteration dumps out of git.** The working CAD tree is hundreds of megabytes of variants;
this folder is for the revisions that were actually built. Anything over 50 MB should go in a
GitHub release or Git LFS rather than the repo.

Print settings, tolerances and post-processing notes belong in each revision's `README.md`.
