# 安装指南 - HPC管理系统

本文档提供了安装HPC管理系统的多种方法。

## 方法一：使用可执行文件安装（推荐）

我们提供了预编译的可执行文件，用户可以直接下载并运行，无需安装Python或任何依赖。

### Windows用户

1. 下载`HPC管理系统-Windows.zip`
2. 解压缩文件
3. 双击运行`HPC管理系统.exe`

### macOS用户

1. 下载`HPC管理系统-macOS.zip`
2. 解压缩文件
3. 将`HPC管理系统.app`拖到应用程序文件夹
4. 从启动台或应用程序文件夹打开应用

### Linux用户

1. 下载`HPC管理系统-Linux.zip`
2. 解压缩文件
3. 打开终端，进入解压目录
4. 运行 `chmod +x ./HPC管理系统`
5. 运行 `./HPC管理系统`

## 方法二：使用pip安装

如果您已经安装了Python 3.8或更高版本，可以使用pip安装：

```bash
# 推荐：创建虚拟环境
python -m venv hpc-env
source hpc-env/bin/activate  # Linux/macOS
# 或
hpc-env\Scripts\activate  # Windows

# 安装
pip install hpc-management-system-1.0.0.tar.gz

# 运行
hpc_management
```

## 方法三：使用conda安装

如果您使用Anaconda或Miniconda管理Python环境：

```bash
# 创建新环境
conda env create -f environment.yml

# 激活环境
conda activate hpc-mgmt

# 运行
hpc_management
```

## 故障排除

如果遇到任何问题，请查看以下常见问题：

1. **登录失败**：确保网络连接稳定，并验证UCI ID和密码是否正确
2. **无法连接到HPC服务器**：检查您的网络连接，确保可以访问HPC服务器
3. **图形界面问题**：确保您的系统满足最低图形要求
   - Windows: Windows 8或更高版本
   - macOS: 10.14或更高版本
   - Linux: 支持X11或Wayland

如需进一步的帮助，请联系技术支持。 