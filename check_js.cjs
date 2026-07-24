const fs = require('fs');
const content = fs.readFileSync('c:/project5/fianl4/community.html', 'utf8');
const match = content.match(/<script>([\s\S]*?)<\/script>/);

if (match) {
  const jsCode = match[1];
  const lines = jsCode.split('\n');
  
  // 尝试逐行找出问题
  for (let i = 1; i < lines.length; i++) {
    const partialCode = lines.slice(0, i).join('\n');
    // 尝试添加闭合括号让它成为一个函数
    const testCode = `function test() {\n${partialCode}\n}`;
    
    try {
      new Function(testCode);
    } catch(e) {
      if (e.message.includes('Unexpected token')) {
        console.log(`Error at line ${i + 250}: ${e.message}`);
        console.log(`Problem line: ${lines[i]}`);
        break;
      }
    }
  }
}
