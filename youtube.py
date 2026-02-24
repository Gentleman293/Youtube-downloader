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


class DownloaderWorker(QObject):
    progress_changed = Signal(int)
    log_message = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, url: str, output_dir: Path, mode: str):
        super().__init__()
        self.url = url.strip()
        self.output_dir = output_dir
        self.mode = mode

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
                    # Делаем упор на максимально совместимый mp4 для Windows-плееров:
                    # H.264 (avc1) + AAC (mp4a) приоритетнее, чем AV1/VP9.
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
        missing = []

        if not self._is_ffmpeg_available():
            missing.append("ffmpeg (бинарник)")

        if missing:
            QMessageBox.warning(
                self,
                "Зависимости",
                "Не найдены: " + ", ".join(missing) + "\n"
                "Установите их и добавьте ffmpeg в PATH.",
            )

    def _is_ffmpeg_available(self) -> bool:
        try:
            ffmpeg.probe
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
        self.worker = DownloaderWorker(url, output_dir, mode)
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
