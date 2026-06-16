#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
处理图标脚本 - 调整大小并添加圆角
"""

import os
from PIL import Image, ImageDraw

def create_mac_icon(input_path, output_path):
    """创建符合macOS格式的应用图标"""
    
    # 打开原始图像
    original = Image.open(input_path)
    
    # 创建图标集合目录
    icon_dir = "hpc3_launcher/resources/icon.iconset"
    os.makedirs(icon_dir, exist_ok=True)
    
    # 图标尺寸列表
    icon_sizes = [16, 32, 64, 128, 256, 512, 1024]
    
    # 对于每个尺寸生成图标
    for size in icon_sizes:
        # 16x16, 16x16@2x, ...
        icon = original.resize((size, size), Image.LANCZOS)
        icon_rounded = add_rounded_corners(icon, size//5)  # 圆角半径约为尺寸的1/5
        icon_rounded.save(f"{icon_dir}/icon_{size}x{size}.png")
        
        # 对于Retina显示(@2x)，如果尺寸允许
        if size*2 <= 1024:
            icon2x = original.resize((size*2, size*2), Image.LANCZOS)
            icon2x_rounded = add_rounded_corners(icon2x, size*2//5)
            icon2x_rounded.save(f"{icon_dir}/icon_{size}x{size}@2x.png")
    
    # 生成.icns文件 (macOS)
    os.system(f"iconutil -c icns {icon_dir} -o {output_path}")
    
    # 创建特殊尺寸的Windows和Linux图标
    win_icon = original.resize((256, 256), Image.LANCZOS)
    win_icon_rounded = add_rounded_corners(win_icon, 50)
    win_icon_rounded.save("hpc3_launcher/resources/icon.png")
    
    # 尝试为Windows创建.ico文件 (如果有合适的库)
    try:
        win_icon_rounded.save("hpc3_launcher/resources/icon.ico", format='ICO')
    except Exception as e:
        print(f"无法创建ICO文件: {e}")
    
    print(f"图标已生成: {output_path}")

def add_rounded_corners(im, radius):
    """给图像添加圆角"""
    circle = Image.new('L', (radius * 2, radius * 2), 0)
    draw = ImageDraw.Draw(circle)
    draw.ellipse((0, 0, radius * 2, radius * 2), fill=255)
    
    size = im.size
    mask = Image.new('L', size, 255)
    
    # 左上角
    mask.paste(circle.crop((0, 0, radius, radius)), (0, 0))
    # 右上角
    mask.paste(circle.crop((radius, 0, radius * 2, radius)), (size[0] - radius, 0))
    # 左下角
    mask.paste(circle.crop((0, radius, radius, radius * 2)), (0, size[1] - radius))
    # 右下角
    mask.paste(circle.crop((radius, radius, radius * 2, radius * 2)), (size[0] - radius, size[1] - radius))
    
    result = im.copy()
    result.putalpha(mask)
    
    return result

if __name__ == "__main__":
    # 设置输入和输出路径
    input_icon = "hpc3_launcher/resources/originalicon.png"
    output_icns = "hpc3_launcher/resources/icon.icns"
    
    # 处理图标
    create_mac_icon(input_icon, output_icns) 