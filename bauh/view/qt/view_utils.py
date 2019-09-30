from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap


def load_icon(path: str, size: int) -> QIcon:
    pixmap = QPixmap(path)
    return load_icon_from(pixmap, size)


def load_icon_from(pixmap: QPixmap, size: int) -> QIcon:
    return QIcon(pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
