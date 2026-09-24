"""Create a repeatable, nested file tree for FolderLens scan measurements."""

import argparse
from pathlib import Path
from typing import Callable, Optional


def create_dataset(root, files: int, files_per_folder: int = 2_000,
                   folders_per_bucket: int = 100, max_bytes: int = 256,
                   on_progress: Optional[Callable[[int, int], None]] = None) -> dict:
    """Create deterministic files without changing an existing nonempty path."""
    target = Path(root)
    if files < 1 or files_per_folder < 1 or folders_per_bucket < 1 or max_bytes < 0:
        raise ValueError("files and folder limits must be positive; max_bytes cannot be negative")
    if target.is_symlink():
        raise ValueError("dataset root cannot be a symbolic link")
    if target.exists():
        if not target.is_dir():
            raise ValueError("dataset root must be a directory")
        if next(target.iterdir(), None) is not None:
            raise ValueError("dataset root must be empty")
    else:
        target.mkdir(parents=True)

    total_bytes = 0
    current_folder = None
    for index in range(files):
        folder_index = index // files_per_folder
        bucket, folder = divmod(folder_index, folders_per_bucket)
        folder_path = target / f"bucket-{bucket:03d}" / f"folder-{folder:03d}"
        if folder_path != current_folder:
            folder_path.mkdir(parents=True, exist_ok=True)
            current_folder = folder_path

        size = (index * 37) % (max_bytes + 1)
        path = folder_path / f"item-{index + 1:07d}.bin"
        with path.open("xb") as stream:
            if size:
                stream.write(bytes((65 + index % 26,)) * size)
        total_bytes += size
        completed = index + 1
        if on_progress is not None:
            on_progress(completed, files)

    return {"root": str(target), "files": files, "logical_bytes": total_bytes,
            "files_per_folder": files_per_folder,
            "folders_per_bucket": folders_per_bucket, "max_bytes": max_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="New or empty directory to populate")
    parser.add_argument("--files", type=int, required=True,
                        help="Number of files to create, such as 10000, 100000, or 1000000")
    parser.add_argument("--files-per-folder", type=int, default=2_000)
    parser.add_argument("--folders-per-bucket", type=int, default=100)
    parser.add_argument("--max-bytes", type=int, default=256,
                        help="Largest deterministic file size in bytes (default: 256)")
    parser.add_argument("--progress-every", type=int, default=50_000)
    args = parser.parse_args()
    if args.progress_every < 1:
        parser.error("--progress-every must be positive")

    def report_progress(done, total):
        if done == total or done % args.progress_every == 0:
            print(f"Created {done:,}/{total:,} files", flush=True)

    try:
        result = create_dataset(
            args.root, args.files, args.files_per_folder,
            args.folders_per_bucket, args.max_bytes, report_progress)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Dataset ready: {result['root']} · {result['files']:,} files · "
          f"{result['logical_bytes']:,} logical bytes")


if __name__ == "__main__":
    main()
