import subprocess
import sys
from pathlib import Path

import ffmpeg
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from yt_dlp import YoutubeDL


def find_ffmpeg_location() -> Path | None:
    """Возвращает папку с ffmpeg/ffprobe для yt-dlp (PATH, рядом с exe, _MEIPASS)."""
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.extend([exe_dir, exe_dir / "bin"])

        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            meipass_dir = Path(meipass)
            candidates.extend([meipass_dir, meipass_dir / "bin"])

    candidates.append(Path.cwd())
    candidates.append(Path.cwd() / "bin")

    for base in candidates:
        ffmpeg_bin = base / "ffmpeg.exe"
        ffprobe_bin = base / "ffprobe.exe"
        if ffmpeg_bin.exists() and ffprobe_bin.exists():
            return base

    # fallback на системный PATH
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return None
    except Exception:
        return None


class DownloaderWorker(QObject):
    progress_changed = Signal(int)
    log_message = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, output_dir: Path, mode: str, ffmpeg_location: Path | None):
        super().__init__()
        self.url = url.strip()
        self.output_dir = output_dir
        self.mode = mode
        self.ffmpeg_location = ffmpeg_location

    def run(self) -> None:
        if not self.url:
            self.failed.emit("Вставьте ссылку на видео.")
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "outtmpl": str(self.output_dir / "%(title)s.%(ext)s"),
            "progress_hooks": [self._progress_hook],
            "noprogress": True,
            "quiet": True,
        }

        if self.ffmpeg_location:
            ydl_opts["ffmpeg_location"] = str(self.ffmpeg_location)
            self.log_message.emit(f"ffmpeg: {self.ffmpeg_location}")

        if self.mode == "audio":
            ydl_opts.update(
                {
                    "format": "bestaudio/best",
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ],
                }
            )
        else:
            ydl_opts.update(
                {
                    "format": (
                        "bestvideo[vcodec^=avc1][ext=mp4]+bestaudio[acodec^=mp4a][ext=m4a]/"
                        "best[vcodec^=avc1][ext=mp4]/best[ext=mp4]/best"
                    ),
                    "merge_output_format": "mp4",
                }
            )

        try:
            self.log_message.emit("Начинаю загрузку...")
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.url, download=True)
                title = info.get("title", "video")

            self.progress_changed.emit(100)
            self.log_message.emit("Загрузка завершена.")
            self.finished.emit(title)
        except Exception as exc:
            self.failed.emit(f"Ошибка загрузки: {exc}")

    def _progress_hook(self, data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            if total:
                pct = int(downloaded / total * 100)
                self.progress_changed.emit(max(0, min(100, pct)))
        elif status == "finished":
            self.progress_changed.emit(95)
            self.log_message.emit("Файл скачан, выполняю постобработку...")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("YouTube Downloader (basic)")
        self.resize(700, 420)

        self.thread: QThread | None = None
        self.worker: DownloaderWorker | None = None
        self.ffmpeg_location = find_ffmpeg_location()

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Вставьте ссылку на YouTube видео")

        self.path_input = QLineEdit(str(Path.cwd() / "downloads"))
        self.path_button = QPushButton("Выбрать папку")
        self.path_button.clicked.connect(self.choose_folder)

        self.mode_box = QComboBox()
        self.mode_box.addItem("Видео (mp4)", "video")
        self.mode_box.addItem("Аудио (mp3)", "audio")

        self.download_button = QPushButton("Скачать")
        self.download_button.clicked.connect(self.start_download)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)

        self._build_layout()
        self._check_dependencies()

    def _build_layout(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(QLabel("Ссылка:"))
        layout.addWidget(self.url_input)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path_input)
        path_row.addWidget(self.path_button)

        layout.addWidget(QLabel("Папка сохранения:"))
        layout.addLayout(path_row)

        layout.addWidget(QLabel("Режим:"))
        layout.addWidget(self.mode_box)

        layout.addWidget(self.download_button)
        layout.addWidget(self.progress_bar)
        layout.addWidget(QLabel("Лог:"))
        layout.addWidget(self.log_box)

    def _check_dependencies(self) -> None:
        if self._is_ffmpeg_available():
            return

        QMessageBox.warning(
            self,
            "Зависимости",
            "Не найден ffmpeg/ffprobe.\n"
            "Для portable-сборки положите ffmpeg.exe и ffprobe.exe рядом с .exe или в папку bin.",
        )

    def _is_ffmpeg_available(self) -> bool:
        try:
            ffmpeg.probe
            if self.ffmpeg_location:
                subprocess.run(
                    [str(self.ffmpeg_location / "ffmpeg.exe"), "-version"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run(
                    ["ffmpeg", "-version"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            return True
        except Exception:
            return False

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            self.path_input.setText(folder)

    def start_download(self) -> None:
        if self.thread and self.thread.isRunning():
            QMessageBox.information(self, "Загрузка", "Дождитесь завершения текущей загрузки.")
            return

        url = self.url_input.text().strip()
        output_dir = Path(self.path_input.text().strip())
        mode = self.mode_box.currentData()

        self.progress_bar.setValue(0)
        self.log_box.clear()
        self.download_button.setEnabled(False)

        self.thread = QThread()
        self.worker = DownloaderWorker(url, output_dir, mode, self.ffmpeg_location)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.progress_changed.connect(self.progress_bar.setValue)
        self.worker.log_message.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.failed.connect(self.on_failed)

        self.worker.finished.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self.cleanup_thread)

        self.thread.start()

    def append_log(self, message: str) -> None:
        self.log_box.appendPlainText(message)

    def on_finished(self, title: str) -> None:
        self.append_log(f"Готово: {title}")
        self.download_button.setEnabled(True)
        QMessageBox.information(self, "Успех", "Загрузка завершена.")

    def on_failed(self, error: str) -> None:
        self.append_log(error)
        self.download_button.setEnabled(True)
        QMessageBox.critical(self, "Ошибка", error)

    def cleanup_thread(self) -> None:
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        if self.thread:
            self.thread.deleteLater()
            self.thread = None


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
