from pathlib import Path

import pytest

from app.importer.sources import LocalTreeSource, folder_segments


def test_local_tree_source_iterates_supported_files(tmp_path: Path):
    (tmp_path / "Beach House" / "Electricity" / "2024").mkdir(parents=True)
    (tmp_path / "Beach House" / "Electricity" / "2024" / "origin.pdf").write_bytes(b"PDF")
    (tmp_path / "Beach House" / "Water").mkdir()
    (tmp_path / "Beach House" / "Water" / "council.png").write_bytes(b"PNG")
    (tmp_path / "Beach House" / "notes.txt").write_text("ignore me")
    (tmp_path / ".DS_Store").write_text("ignore")

    items = list(LocalTreeSource(tmp_path).iter_items())
    paths = sorted(it.relative_path for it in items)
    assert paths == [
        "Beach House/Electricity/2024/origin.pdf",
        "Beach House/Water/council.png",
    ]


def test_local_tree_source_lazy_loader(tmp_path: Path):
    (tmp_path / "x.pdf").write_bytes(b"hello")
    (item,) = list(LocalTreeSource(tmp_path).iter_items())
    assert item.filename == "x.pdf"
    assert item.bytes_loader() == b"hello"


def test_local_tree_source_missing_root(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        LocalTreeSource(tmp_path / "does-not-exist")


def test_folder_segments():
    assert folder_segments("a/b/c.pdf") == ["a", "b"]
    assert folder_segments("only.pdf") == []


def test_local_tree_skips_unsupported_extensions(tmp_path: Path):
    (tmp_path / "a.pdf").write_bytes(b"PDF")
    (tmp_path / "b.docx").write_bytes(b"DOC")  # not in SUPPORTED_EXTENSIONS
    items = list(LocalTreeSource(tmp_path).iter_items())
    assert [it.filename for it in items] == ["a.pdf"]
