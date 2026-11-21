"""Build a Telegram Exchange v1 ZIP before the caller publishes it."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from services.exchange_export import ExchangeExportService


class EmptyTelegramPackage(ValueError):
    """The selected category contains no exportable resources."""


@contextmanager
def stage_tg_package(exporter: ExchangeExportService, folder, category, source_url, progress):
    # Stage on the destination volume so publishing can be atomic.
    with TemporaryDirectory(prefix="tg_package_", dir=folder) as staging:
        temporary = Path(staging) / "package.zip"
        manifest = exporter.export_zip(
            str(temporary), selected_categories=[category], export_scope="categories",
            package_id=category, progress_callback=progress,
        )
        if not manifest["counts"]["resources"]:
            raise EmptyTelegramPackage()
        if source_url:
            with ZipFile(temporary, "a", compression=ZIP_DEFLATED) as archive:
                archive.writestr("TG.txt", source_url.strip() + "\n")
        yield temporary
