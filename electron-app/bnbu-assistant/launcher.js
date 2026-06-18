/**
 * BNBU 启动器 - 解决 ELECTRON_RUN_AS_NODE 环境变量冲突
 * 用法: node launcher.js
 */
const { spawn } = require('child_process');
const path = require('path');

// 强制清除冲突的环境变量
delete process.env.ELECTRON_RUN_AS_NODE;
delete process.env.NODE_OPTIONS;
delete process.env.ACC_PRODUCT_CONFIG_V3;
delete process.env.ACC_PRODUCT_CONFIG_PATH;
delete process.env.ACC_PRODUCT_CONFIG;

// 确保子进程也不继承这些变量
const cleanEnv = { ...process.env };
delete cleanEnv.ELECTRON_RUN_AS_NODE;
delete cleanEnv.NODE_OPTIONS;

const electronPath = path.join(__dirname, 'node_modules', 'electron', 'dist', 'electron.exe');
const child = spawn(electronPath, ['.'], {
    stdio: 'inherit',
    env: cleanEnv,
    windowsHide: false
});

child.on('close', (code) => {
    process.exit(code);
});
