import csv
import io
import tempfile
import os
import aiofiles


async def build_csv_file(tracks: list[dict], filename: str = "tracks.csv") -> str:
    """
    Write tracks to a temp .csv file (artist, title, album, year).
    Returns the path to the file.
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".csv",
        prefix="export_",
        delete=False,
        encoding="utf-8-sig",  # BOM for Excel compatibility
        newline="",
    )
    tmp_path = tmp.name
    tmp.close()

    async with aiofiles.open(tmp_path, "w", encoding="utf-8-sig", newline="") as f:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=["artist", "title", "album", "year"],
                                extrasaction="ignore")
        writer.writeheader()
        for t in tracks:
            writer.writerow({
                "artist": t.get("artist", ""),
                "title": t.get("title", ""),
                "album": t.get("album", ""),
                "year": t.get("year", ""),
            })
        await f.write(buf.getvalue())

    return tmp_path


async def build_txt_file(tracks: list[dict], filename: str = "tracks.txt") -> str:
    """
    Write tracks to a temp .txt file.
    Returns the path to the file.
    Format: Исполнитель — Название
    """
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".txt",
        prefix="export_",
        delete=False,
        encoding="utf-8",
    )
    tmp_path = tmp.name
    tmp.close()

    async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
        lines = [f"{t['artist']} \u2014 {t['title']}" for t in tracks]
        await f.write("\n".join(lines))

    return tmp_path


async def cleanup(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
