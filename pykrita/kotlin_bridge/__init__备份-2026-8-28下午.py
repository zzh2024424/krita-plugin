from krita import Krita
from .kotlin_bridge import KotlinBridgeExtension

# 将你的扩展类注册到 Krita 实例中
#Krita.instance().addExtension(KotlinBridgeExtension(Krita.instance()))
#2026-7-2将第4行注释掉