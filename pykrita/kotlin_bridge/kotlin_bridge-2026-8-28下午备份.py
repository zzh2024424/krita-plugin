import os
import socket
import subprocess
import threading
import traceback
import tempfile

from krita import Extension, Krita
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QImage

class KritaSignalHelper(QObject):
    import_signal = pyqtSignal(str)

class KotlinBridgeExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server_thread = None
        self.server_socket = None
        self.running_port = 0

        self.log_path = os.path.join(os.environ.get("TEMP", "/tmp"), "krita_bridge.log")

        # 将端口写入临时文件，桌面端可读取该文件获取端口
        self.port_file_path = os.path.join(tempfile.gettempdir(), "krita_port.txt")
        self.handshake_bytes = b"KritaBridgeV1\n"

        self.helper = KritaSignalHelper()
        self.helper.import_signal.connect(self.import_image_to_krita)

    def setup(self):
        pass

    def log(self, message):
        line = f"{message}"
        print(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def createActions(self, window):
        action = window.createAction(
            "start_kotlin_bridge",
            "启动 Kotlin 桥梁程序",
            "tools/scripts"
        )
        action.triggered.connect(self.start_bridge)

    def start_bridge(self):
        self.stop_bridge()
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()
        QMessageBox.information(None, "提示", "已启动服务，正在拉起客户端...")

    def stop_bridge(self):
        # 关闭 socket
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None

        # 移除端口文件（如果存在）
        try:
            if os.path.exists(self.port_file_path):
                os.remove(self.port_file_path)
                self.log(f"[Krita] 已删除端口文件: {self.port_file_path}")
        except Exception:
            pass

        # 移除 adb reverse 映射（如果有）
        if self.running_port != 0:
            try:
                subprocess.run(
                    ["adb", "reverse", "--remove", f"tcp:{self.running_port}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
            self.running_port = 0

    def run_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 只监听本机回环
            self.server_socket.bind(("127.0.0.1", 0))
            self.running_port = self.server_socket.getsockname()[1]

            # 把端口写到临时文件，供桌面端快速发现
            try:
                with open(self.port_file_path, "w", encoding="utf-8") as f:
                    f.write(str(self.running_port))
                self.log(f"[Krita] 已写端口文件: {self.port_file_path} -> {self.running_port}")
            except Exception as e:
                self.log(f"[Krita] 写端口文件失败: {e}")

            self.server_socket.listen(1)

            # helper: 检测是否存在可用 adb 设备（已授权并处于 device 状态）
            def _adb_has_device():
                try:
                    proc = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=5)
                    out = proc.stdout.strip()
                    for line in out.splitlines():
                        line = line.strip()
                        if not line or line.startswith("List of devices"):
                            continue
                        # 匹配 "<serial>\tdevice"
                        if "\tdevice" in line:
                            return True
                    return False
                except Exception as e:
                    self.log(f"[Krita] 检查 adb 设备时异常: {e}")
                    return False

            has_device = _adb_has_device()
            if not has_device:
                # 没有设备时，跳过 adb reverse 与启动 App（桌面开发场景常见）
                self.log("[Krita] 未检测到任何 adb 设备，跳过 adb reverse 与启动 App（在桌面模式下这是正常的）")
            else:
                # 如果检测到设备，尝试建立 adb reverse；捕获 stdout/stderr 并记录，不抛异常
                self.log(f"[Krita] 检测到 adb 设备，尝试建立 adb reverse: {self.running_port}")
                try:
                    result = subprocess.run(
                        ["adb", "reverse", f"tcp:{self.running_port}", f"tcp:{self.running_port}"],
                        capture_output=True,
                        text=True,
                        timeout=8
                    )
                    if result.returncode == 0:
                        self.log(f"[Krita] adb reverse 成功: tcp:{self.running_port} -> tcp:{self.running_port}")
                    else:
                        self.log(f"[Krita] adb reverse 返回非0: returncode={result.returncode}")
                        if result.stdout:
                            self.log(f"[Krita] adb reverse stdout: {result.stdout.strip()}")
                        if result.stderr:
                            self.log(f"[Krita] adb reverse stderr: {result.stderr.strip()}")
                        self.log("[Krita] 继续运行（adb reverse 失败不会导致退出），如需 Android 支持请检查 adb devices 的输出")
                except subprocess.TimeoutExpired:
                    self.log("[Krita] adb reverse 超时（8s），继续运行（可能是 adb 卡住）")
                except Exception as e:
                    self.log(f"[Krita] adb reverse 调用异常: {e}")

                # 如果检测到设备，尝试通过 adb 启动 App（非阻塞）
                try:
                    adb_command = [
                        "adb", "shell", "am", "start",
                        "-n", "com.example.datato3d/.MainActivity",
                        "--ei", "krita_port", str(self.running_port)
                    ]
                    self.log("[Krita] 正在通过 adb 启动 App")
                    subprocess.Popen(adb_command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception as e:
                    self.log(f"[Krita] 通过 adb 启动 App 失败: {e}")

            # 等待客户端连接（无论是否做过 adb reverse，都在本机监听）
            conn, addr = self.server_socket.accept()
            self.log(f"[Krita] 客户端已成功连接: {addr}")

            # 主动发送握手标识，方便客户端用短连接验证该服务
            try:
                conn.sendall(self.handshake_bytes)
                self.log(f"[Krita] 已发送握手: {self.handshake_bytes!r}")
            except Exception as e:
                self.log(f"[Krita] 发送握手失败: {e}")

            buffer = ""
            while True:
                data = conn.recv(1024)
                self.log(f"[Krita] recv raw = {data!r}")

                if not data:
                    self.log("[Krita] socket 已关闭")
                    break

                buffer += data.decode("utf-8", errors="ignore")

                while "\n" in buffer:
                    message, buffer = buffer.split("\n", 1)
                    message = message.strip()
                    if not message:
                        continue

                    self.log(f"[Krita] 收到消息: {message}")

                    if message.startswith("SEND_IMAGE:"):
                        android_path = message.replace("SEND_IMAGE:", "").strip()
                        self.log(f"[Krita] 准备导入图片路径: {android_path}")
                        # 异步拉取图片以免阻塞 socket 线程
                        t = threading.Thread(target=self.download_image_background, args=(android_path,), daemon=True)
                        t.start()
                    else:
                        self.log(f"[Krita] 未识别消息: {message}")

            conn.close()

        except Exception as e:
            self.log(f"[Krita] Socket线程出错: {e}")
            self.log(traceback.format_exc())
        finally:
            # 确保清理端口文件与 adb reverse
            try:
                self.stop_bridge()
            except Exception:
                pass

    def download_image_background(self, android_path):
        try:
            pc_temp_dir = os.environ.get("TEMP", r"C:\temp")
            pc_image_path = os.path.join(pc_temp_dir, "krita_received.png")

            if not os.path.exists(pc_temp_dir):
                os.makedirs(pc_temp_dir)

            if os.path.exists(pc_image_path):
                try:
                    os.remove(pc_image_path)
                except Exception:
                    pass

            self.log(f"[Krita] 正在从安卓拉取文件: {android_path} -> {pc_image_path}")
            pull_cmd = ["adb", "pull", android_path, pc_image_path]
            result = subprocess.run(pull_cmd, capture_output=True, text=True)

            self.log(f"[Krita] adb pull returncode = {result.returncode}")
            if result.stdout:
                self.log(f"[Krita] adb pull stdout: {result.stdout.strip()}")
            if result.stderr:
                self.log(f"[Krita] adb pull stderr: {result.stderr.strip()}")

            if result.returncode == 0 and os.path.exists(pc_image_path):
                self.log("[Krita] 成功下载到本地电脑，通知主线程导入...")
                self.helper.import_signal.emit(pc_image_path)
            else:
                self.log("[Krita] adb pull 拉取失败，文件未生成或返回码非0")

        except Exception as e:
            self.log(f"[Krita] download_image_background 发生报错: {e}")
            self.log(traceback.format_exc())

    def import_image_to_krita(self, pc_image_path):
        try:
            self.log(f"[Krita] 开始导入图片: {pc_image_path}")

            if not os.path.exists(pc_image_path):
                self.log(f"[Krita] 文件不存在: {pc_image_path}")
                return

            doc = Krita.instance().activeDocument()
            if doc is None:
                self.log("[Krita] 画布检查失败：当前 Krita 没有打开任何文件！")
                return

            self.log(f"[Krita] doc = {doc.colorModel()} / {doc.colorDepth()} / {doc.colorProfile()}")

            image = QImage(pc_image_path)
            if image.isNull():
                self.log("[Krita] QImage 读取失败，图片可能损坏或格式不支持")
                return

            self.log(f"[Krita] image size = {image.width()} x {image.height()}, format = {image.format()}")

            image = image.convertToFormat(QImage.Format_ARGB32)

            width = image.width()
            height = image.height()

            ptr = image.bits()
            ptr.setsize(image.byteCount())
            pixel_data = bytes(ptr)

            root = doc.rootNode()
            new_layer = doc.createNode("Android_Output", "paintLayer")
            if new_layer is None:
                self.log("[Krita] createNode 失败")
                return

            root.addChildNode(new_layer, None)

            success = new_layer.setPixelData(pixel_data, 0, 0, width, height)

            if success:
                doc.refreshProjection()
                self.log("[Krita] 图片已成功导入为新图层: Android_Output")
            else:
                self.log("[Krita] setPixelData 失败，未写入图层")

        except Exception as e:
            self.log(f"[Krita] 主线程操作异常: {e}")
            self.log(traceback.format_exc())

Krita.instance().addExtension(KotlinBridgeExtension(Krita.instance()))