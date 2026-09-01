import os
import socket
import subprocess
import threading
import traceback

from krita import Extension, Krita
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QObject, pyqtSignal, QStandardPaths
from PyQt5.QtGui import QImage

class KritaSignalHelper(QObject):
    import_signal = pyqtSignal(str)

class KotlinBridgeExtension(Extension):
    def __init__(self, parent, device_id="1e52088a"):  # 默认设备ID，实际使用时传入正确的
        super().__init__(parent)

        self.device_id = device_id  # 保存设备ID

        self.server_thread = None
        self.server_socket = None
        self.running_port = 0
        self.conn = None  # 保存客户端socket连接，用于双向通信

        self.log_path = os.path.join(os.environ.get("TEMP", "/tmp"), "krita_bridge.log")

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
        # 启动 Kotlin 桥梁程序 菜单
        action_start = window.createAction(
            "start_kotlin_bridge",
            "启动 Kotlin 桥梁程序",
            "tools/scripts"
        )
        action_start.triggered.connect(self.start_bridge)

        # 发送当前画布到安卓 菜单
        action_send = window.createAction(
            "send_image_to_android",
            "发送当前画布到安卓",
            "tools/scripts"
        )
        action_send.triggered.connect(self.send_current_image_to_android)

    def start_bridge(self):
        self.stop_bridge()
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()
        QMessageBox.information(None, "提示", "已启动服务，正在拉起客户端...")

    def stop_bridge(self):
        try:
            if self.server_socket:
                self.server_socket.close()
                self.server_socket = None
        except Exception:
            pass

        try:
            if self.conn:
                self.conn.close()
                self.conn = None
        except Exception:
            pass

        if self.running_port != 0:
            try:
                subprocess.run(
                    [
                        "adb",
                        "-s",
                        self.device_id,
                        "reverse",
                        "--remove",
                        f"tcp:{self.running_port}",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass
            self.running_port = 0

    def run_server(self):
        try:
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.bind(("127.0.0.1", 0))
            self.running_port = self.server_socket.getsockname()[1]
            self.server_socket.listen(1)

            self.log(f"[Krita] 正在建立 adb 逆向映射端口: {self.running_port}")
            subprocess.run(
                [
                    "adb",
                    "-s",
                    self.device_id,
                    "reverse",
                    f"tcp:{self.running_port}",
                    f"tcp:{self.running_port}"
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            adb_command = [
                "adb",
                "-s",
                self.device_id,
                "shell",
                "am",
                "start",
                "-n",
                "com.example.datato3d/.MainActivity",
                "--ei",
                "krita_port",
                str(self.running_port),
            ]
            self.log("[Krita] 正在通过 adb 启动 App")
            subprocess.Popen(adb_command)

            self.conn, addr = self.server_socket.accept()
            self.log(f"[Krita] 安卓客户端已成功连接: {addr}")

            buffer = ""
            while True:
                data = self.conn.recv(1024)
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

                    self.log(f"[Krita] 收到安卓消息: {message}")

                    if message.startswith("SEND_IMAGE:"):
                        android_path = message.replace("SEND_IMAGE:", "").strip()
                        self.log(f"[Krita] 准备导入图片路径: {android_path}")
                        self.download_image_background(android_path)
                    else:
                        self.log(f"[Krita] 未识别消息: {message}")

        except Exception as e:
            self.log(f"[Krita] Socket线程出错: {e}")
            self.log(traceback.format_exc())
        finally:
            self.stop_bridge()

    def download_image_background(self, android_path):
        try:
            pc_temp_dir = os.environ.get("TEMP", r"C:\temp")
            pc_image_path = os.path.join(pc_temp_dir, "krita_received.jpg")

            if not os.path.exists(pc_temp_dir):
                os.makedirs(pc_temp_dir)

            if os.path.exists(pc_image_path):
                try:
                    os.remove(pc_image_path)
                except Exception:
                    pass

            self.log(f"[Krita] 正在从安卓拉取文件: {android_path} -> {pc_image_path}")
            pull_cmd = [
                "adb",
                "-s",
                self.device_id,
                "pull",
                android_path,
                pc_image_path,
            ]
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

    def send_current_image_to_android(self):
        doc = Krita.instance().activeDocument()
        if not doc:
            QMessageBox.warning(None, "提示", "没有打开的文档，无法发送图片")
            return

        temp_path = os.path.join(QStandardPaths.writableLocation(QStandardPaths.TempLocation), "krita_send.png")

        try:
            # ✅ 正确创建 InfoObject（Krita 5.3+ 推荐方式）
            export_info = Krita.instance().createInfoObject()
            result = doc.exportImage(temp_path, export_info)
        except Exception as e:
            self.log(f"[Krita] 导出图片失败（尝试 createInfoObject）: {e}")
            try:
                # 备用：如果 createInfoObject 不可用，用空字典（极少数旧构建）
                result = doc.exportImage(temp_path, {})
            except Exception as e2:
                self.log(f"[Krita] 导出失败（备用方案也失败）: {e2}")
                QMessageBox.warning(None, "错误", f"导出图片失败：{e}")
                return

        if not result:
            self.log("[Krita] 导出图片失败")
            QMessageBox.warning(None, "提示", "导出当前画布图片失败")
            return

        self.log(f"[Krita] 已导出图片到临时路径: {temp_path}")

        if self.conn:
            try:
                self.send_image_via_socket(temp_path)
                QMessageBox.information(None, "提示", "图片已发送到安卓")
            except Exception as e:
                self.log(f"[Krita] 发送图片异常: {e}")
                QMessageBox.warning(None, "错误", f"发送图片失败: {e}")
        else:
            QMessageBox.warning(None, "提示", "未连接安卓客户端，无法发送")

    def send_image_via_socket(self, image_path):
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片文件不存在: {image_path}")

        with open(image_path, "rb") as f:
            data = f.read()

        size = len(data)
        # 先发送协议头告诉安卓接收大小
        protocol_msg = f"SEND_IMAGE_START:{size}\n"
        self.conn.send(protocol_msg.encode("utf-8"))

        # 发送图片二进制数据
        self.conn.sendall(data)

        self.log("[Krita] 已将当前画布图片发送到安卓客户端")

# 注册扩展到 Krita
Krita.instance().addExtension(KotlinBridgeExtension(Krita.instance()))