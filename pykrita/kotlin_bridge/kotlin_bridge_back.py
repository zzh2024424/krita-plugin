import os
import socket
import subprocess
import threading

from krita import Extension, Krita
from PyQt5.QtWidgets import QMessageBox

class KotlinBridgeExtension(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server_thread = None
        self.server_socket = None
        self.running_port = 0

    def setup(self):
        pass

    def createActions(self, window):
        action = window.createAction(
            "start_kotlin_bridge",
            "启动 Kotlin 桥梁程序",
            "tools/scripts"
        )
        action.triggered.connect(self.start_bridge)

    def start_bridge(self):
        # 若已有服务在运行，先关闭
        self.stop_bridge()

        # 异步启动 Socket
        self.server_thread = threading.Thread(target=self.run_server, daemon=True)
        self.server_thread.start()

        QMessageBox.information(None, "提示", "已重置并启动本地服务，正在拉起客户端...")

    def stop_bridge(self):
        """关闭当前的 Socket 和 adb reverse 映射"""
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None

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
            self.server_socket.bind(("127.0.0.1", 0))
            self.running_port = self.server_socket.getsockname()[1]
            self.server_socket.listen(1)

            # adb reverse: Android 端连 127.0.0.1:<port> 会转发到电脑本机同端口
            print(f"[Krita] 正在建立 adb 逆向映射端口: {self.running_port}")
            subprocess.run(
                ["adb", "reverse", f"tcp:{self.running_port}", f"tcp:{self.running_port}"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # 拉起 App，并把端口作为 extra 传过去
            adb_command = [
                "adb", "shell", "am", "start",
                "-n", "com.example.datato3d/.MainActivity",
                "--ei", "krita_port", str(self.running_port)
            ]

            print("[Krita] 正在通过 adb 启动 App")
            subprocess.Popen(adb_command)

            # 等待 Android 连接
            conn, addr = self.server_socket.accept()
            print(f"[Krita] 安卓客户端已成功连接: {addr}")

            while True:
                data = conn.recv(1024)
                if not data:
                    break

                message = data.decode("utf-8").strip()
                self.run_on_main_thread(message)

            conn.close()

        except Exception as e:
            print(f"[Krita] 报错: {e}")
        finally:
            self.stop_bridge()

    def run_on_main_thread(self, message):
        print(f"[Krita] 收到安卓消息: {message}")
        if message.startswith("SEND_IMAGE:"):
            android_path = message.replace("SEND_IMAGE:", "").strip()
            self.import_image_from_android(android_path)
        else:
            self.process_kotlin_message(message)

    def import_image_from_android(self, android_path):
        try:
            pc_temp_dir = os.environ.get("TEMP", r"C:\temp")
            pc_image_path = os.path.join(pc_temp_dir, "krita_received.png")

            if not os.path.exists(pc_temp_dir):
                os.makedirs(pc_temp_dir)

            print(f"[Krita] 正在拉取文件: {android_path} -> {pc_image_path}")
            pull_cmd = ["adb", "pull", android_path, pc_image_path]
            subprocess.run(pull_cmd, check=True)

            doc = Krita.instance().activeDocument()
            if doc is not None:
                root = doc.rootNode()
                new_layer = doc.createNode("Android_Output", "paintLayer")
                new_layer.importImage(pc_image_path)
                root.addChildNode(new_layer, None)
                doc.refreshProjection()
                print("[Krita] 图片已成功导入为新图层！")
            else:
                print("[Krita] 加载失败：当前没有打开任何 Krita 画布！")

        except Exception as e:
            print(f"[Krita] 拉图或导入失败: {e}")

    def process_kotlin_message(self, message):
        # 其他指令...
        pass

Krita.instance().addExtension(KotlinBridgeExtension(Krita.instance()))