# OCR 依赖安装与故障排除指南

## 适用范围
- 目标依赖：tesseract 与 poppler
- 用途：PDF 转图片与 OCR 识别

## macOS 安装
1. 运行脚本
```
bash bin/install_ocr_macos.sh
```
2. 验证
```
tesseract --version
pdftoppm -v
```

## Windows 安装
1. 以管理员权限运行
```
bin\install_ocr_windows.bat
```
2. 重新打开终端后验证
```
tesseract --version
pdftoppm -v
```

## 环境变量配置
- macOS 使用 Homebrew 安装后自动加入 PATH
- Windows 如未生效，需将以下路径加入系统 PATH
  - C:\Program Files\Tesseract-OCR
  - C:\Program Files\poppler\Library\bin

## OCR 引擎配置
在 app/config.json 中配置
```
"ocr_engine": "auto",
"ocr_engine_order": ["tesseract", "mineru"],
"ocr_engine_by_type": {"pdf": "tesseract"},
"ocr_engines": {"mineru": {"module": "mineru", "function": "ocr_pdf"}}
```

## 验证与测试
1. 依赖检测与基准测试
```
python bin/verify_ocr_env.py --pdf /path/to/sample.pdf --output reports/ocr_report.json
```
2. 生成测试报告
```
python bin/generate_test_report.py reports/test_report.json
```

## 常见问题
1. tesseract 或 pdftoppm 找不到
检查 PATH 是否包含对应安装目录并重新打开终端
2. pdf2image 报错无法转换
确认 poppler 已安装且 pdftoppm 可执行
3. OCR 速度慢
尝试降低 ocr_dpi，或使用更快的 OCR 引擎
4. 中文识别不准确
确认 tesseract 语言包已安装，配置 ocr_languages 为 chi_sim+eng

## Docker 方案
1. 构建并启动
```
docker compose up --build
```
2. 容器内验证
```
docker compose exec backend tesseract --version
docker compose exec backend pdftoppm -v
```
