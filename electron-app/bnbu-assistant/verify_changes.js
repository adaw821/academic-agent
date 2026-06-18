/**
 * 验证 index.html 是否包含必要的修改
 * 运行: node verify_changes.js
 */

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, 'index.html');
const content = fs.readFileSync(htmlPath, 'utf-8');

console.log('=== 验证 index.html 的修改 ===\n');

// 检查1: openGradePanel 函数
const hasOpenGradePanel = content.includes('function openGradePanel()');
const hasImprovedLogic = content.includes("activeView.value !== 'gpa'");
console.log('1. openGradePanel 函数:');
console.log(`   - 函数存在: ${hasOpenGradePanel ? '✅' : '❌'}`);
console.log(`   - 包含改进逻辑: ${hasImprovedLogic ? '✅' : '❌'}`);

// 检查2: 右侧面板不应该有 :class= 条件
const hasHiddenCondition = content.includes(":class=\"activeView==='gpa' ? '' : 'hidden'\"");
console.log('\n2. 右侧面板条件类:');
console.log(`   - 已移除隐藏条件: ${!hasHiddenCondition ? '✅' : '❌'}`);

// 检查3: 面板应该有 fixed 定位
const hasFixedPanel = content.includes('fixed top-0 right-0');
console.log('\n3. 面板定位:');
console.log(`   - 使用 fixed 定位: ${hasFixedPanel ? '✅' : '❌'}`);

// 检查4: 底部收缩箭头
const hasCollapseArrow = content.includes('chevron_right') && content.includes('bottom-0');
console.log('\n4. 底部收缩箭头:');
console.log(`   - 包含收缩箭头: ${hasCollapseArrow ? '✅' : '❌'}`);

console.log('\n=== 总结 ===');
if (hasOpenGradePanel && hasImprovedLogic && !hasHiddenCondition && hasFixedPanel) {
    console.log('✅ 所有修改都已正确应用！');
    console.log('   如果看不到效果，请尝试:');
    console.log('   1. 完全关闭应用（包括后台进程）');
    console.log('   2. 重新运行 start.bat');
} else {
    console.log('❌ 部分修改可能未正确保存！');
    console.log('   请告诉我哪些检查失败了。');
}
