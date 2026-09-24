# Changelog

## 4.0.0

FolderLens 4.0 rebuilds the app around one scan session and one query model.
Every view, export, and file action now reads the same data, so what you see
is what gets exported, zipped, or deleted.

### Scanning
- Scan sessions have their own cancellation and generation, so an old or
  cancelled scan can never overwrite a newer one.
- Live progress while scanning: files seen so far, bytes observed, and a
  provisional list of the largest files, all labeled as incomplete.
- A bounded work queue that does not deadlock, with gentler concurrency on
  network shares.
- **Skip a stuck folder:** when a share stops responding, a *Skip folder*
  button appears. The scan finishes without that folder and is marked partial.
- **Stop reports what is happening:** if the file system is still busy, the
  status bar says the scan is stopping and waiting for it. It only says
  "cancelled" once the scan has actually stopped.
- Hidden files, reparse points, and junctions are recorded but not followed.
  Folders that could not be read keep the result marked partial.
- **On-disk size (opt-in, local drives only):** allocated and unique on-disk
  bytes and hardlink counts, next to the logical size. Values that cannot be
  read are shown as unknown.

### Filters and views
- One shared `QuerySpec` powers Tree, Treemap, Explore, Largest Files, File
  Types, Duplicates, search, and export. Filters can combine categories,
  extensions, name terms, size and date ranges, and hidden-file rules.
- **Filter presets:** built-in presets and your own saved filters.
- Size metric choice (logical or on disk) when the scan measured it.
- File Types also groups files by last-modified age.
- The new **Explore** workspace shows the tree, treemap, and details side by
  side, with selection kept in sync in both directions.
- Treemap: Back and Forward history, keyboard navigation, a details pane, and
  a paged list behind the "smaller items" tile. Rendering runs in the
  background.
- Very wide folders load and sort in pages without blocking the window.

### Safe actions
- ZIP and delete check the selection against the disk first. If the scan is
  out of date, the action stops.
- A filtered view never quietly narrows or widens an action. Prompts show the
  real size and file count of whole folders.
- Protected system locations are refused, and permanent delete is never used
  as a silent fallback.
- Duplicate scans have a read budget and ask before reading file contents on
  network shares.
- Image viewer: images decode in the background, and navigation only moves
  once the next image has loaded. Esc and the close button both warn about
  unsaved annotations, and "Save as" writes the file atomically.

### Reports and automation
- CSV and JSON exports record their scope, filter, metric, and whether the
  scan was partial.
- **Headless reports:** `--json`/`--csv` with filter options. Exit codes: `0`
  complete, `2` partial, `1` failed. The command line never deletes or changes
  files.

### Release and updates
- The version is the same everywhere: tag, `version.py`, Windows file
  metadata, installer name, and `--version`. CI checks this.
- Releases publish a `SHA256SUMS` manifest. The updater checks downloads
  against it, stages the new version, and restores the previous executable if
  the swap fails.
- A versioned Inno Setup installer with optional Explorer context menu. CI
  installs, runs, and uninstalls it.
- Settings are versioned (schema 2) and saved atomically. 3.x settings carry
  over.

### Known limits
- Field measurements on physical SSD/HDD corpora and on a real SMB share,
  and the Windows high-DPI visual pass, are still open. See
  `docs/4.0-release-acceptance.md`.
- On-disk size rounds up to whole clusters, so very small files stored inside
  the NTFS MFT are overstated.
