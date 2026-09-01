import os
import socket
import subprocess
import threading
from krita import Extension, Krita
from PyQt5.QtWidgets import QMessageBox
from PyQt5.QtCore import QObject, pyqtSignal

# 1. 定义信号中转器，用来安全通过线程壁垒传递事件
class KritaSignalHelper(QObject):
    import_signal = pyqtSignal(str) # 定义一个传递字符串(文件路径)的信号

class KotlinBridgeExtension(Extension): 

    def __init__(self, parent): 
        super().__init__(parent) 
        self.server_thread = None
        self.server_socket = None
        self.running_port = 0
        
        # 实例化信号转接器，并把信号绑定到主线程导入逻辑
        self.helper = KritaSignalHelper()
        self.helper.import_signal.connect(self.import_image_to_krita)

    def setup(self): 
        pass

    def createActions(self, window): 
        action = window.createAction("start_kotlin_bridge", "启动 Kotlin 桥梁程序", "tools/scripts") 
        action.triggered.connect(self.start_bridge) 

    def start_bridge(self): 
        self.stop_bridge()
        self.server_thread = threading.Thread(target=self.run_server, daemon=True) 
        self.server_thread.start() 
        QMessageBox.information(None, "提示", "已启动服务，正在拉起客户端...") 

    def stop_bridge(self):
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass
            self.server_socket = None
        
        if self.running_port != 0:
            try:
                # 移除 adb 映射
                subprocess.run(f"adb reverse --remove tcp:{self.running_port}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
            self.running_port = 0

    def run_server(self): 
        try: 
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM) 
            self.server_socket.bind(('127.0.0.1', 0)) 
            self.running_port = self.server_socket.getsockname()[1] 
            self.server_socket.listen(1) 

            print(f"[Krita] 正在建立 adb 逆向映射端口: {self.running_port}")
            subprocess.run(f"adb reverse tcp:{self.running_port} tcp:{self.running_port}", shell=True)

            # 拉起模拟器 App
            adb_command = [ 
                "adb", "shell", "am", "start", 
                "-n", "com.example.datato3d/.MainActivity", 
                "--ei", "krita_port", str(self.running_port)
            ] 
            print(f"[Krita] 正在通过 adb 启动 App") 
            subprocess.Popen(adb_command, shell=True)

            conn, addr = self.server_socket.accept() 
            print(f"[Krita] 安卓客户端已成功连接！") 

            while True: 
                data = conn.recv(1024) 
                if not data: 
                    break
                message = data.decode('utf-8').strip() 
                print(f"[Krita] 收到接收码: {message}")
                
                if message.startswith("SEND_IMAGE:"): 
                    android_path = message.replace("SEND_IMAGE:", "").strip() 
                    # 后台下载图片，不再卡死 UI 线程
                    self.download_image_background(android_path)

            conn.close() 
        except Exception as e: 
            print(f"[Krita] Socket线程出错: {e}") 
        finally: 
            self.stop_bridge()

    def download_image_background(self, android_path):
        """此方法运行在后台子线程中，用于提取手机图片"""
        try:
            pc_temp_dir = os.environ.get('TEMP', 'C:\\temp') 
            pc_image_path = os.path.join(pc_temp_dir, "krita_received.png") 
            
            if not os.path.exists(pc_temp_dir): 
                os.makedirs(pc_temp_dir) 

            # 如果存在老图片，先将其删除，避免拉取失败时误用了旧文件
            if os.path.exists(pc_image_path):
                try: 
                    os.remove(pc_image_path)
                except Exception:
                    pass

            print(f"[Krita] 正在从安卓拉取文件: {android_path} -> {pc_image_path}") 
            pull_cmd = ["adb", "pull", android_path, pc_image_path] 
            result = subprocess.run(pull_cmd, capture_output=True, shell=True) 

            if result.returncode == 0 and os.path.exists(pc_image_path):
                print(f"[Krita] 成功下载到本地电脑，通知主线程导入...")
                # 🚀 触发信号：让主线程安全接管图片导入画布的操作
                self.helper.import_signal.emit(pc_image_path)
            else:
                err_info = result.stderr.decode('utf-8', errors='ignore')
                print(f"[Krita] adb pull 拉取失败: {err_info}")
        except Exception as e:
            print(f"[Krita] download_image_background 发生报错: {e}")

    def import_image_to_krita(self, pc_image_path):
        """【安全】此方法被绑定至信号，自动运行在 Krita 的 Qt 主线程中"""
        try:
            doc = Krita.instance().activeDocument() 
            if doc is not None: 
                root = doc.rootNode() 
                
                # 创建一个全新的图层
                new_layer = doc.createNode("Android_Output", "paintLayer") 
                
                # 导入图片数据到新生成的图层
                success = new_layer.importImage(pc_image_path) 
                
                if success:
                    root.addChildNode(new_layer, None) 
                    doc.refreshProjection() 
                    print("[Krita] 图片已在主线程中成功导入为新图层: Android_Output！") 
                else:
                    print("[Krita] Krita 加载该图片文件失败（文件可能损坏）") 
            else: 
                print("[Krita] 画布检查失败：当前 Krita 没有打开任何文件！") 
                
        except Exception as e: 
            print(f"[Krita] 主线程操作异常: {e}") 

Krita.instance().addExtension(KotlinBridgeExtension(Krita.instance()))