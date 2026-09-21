# 永久关闭 GPU（Ubuntu 推荐）

sudo systemctl edit ollama.service

[Service]
Environment="CUDA_VISIBLE_DEVICES=-1"
Environment="OLLAMA_NUM_GPUS=0"
Environment="OLLAMA_VULKAN=0"


sudo systemctl daemon-reload
sudo systemctl restart ollama