/** 水文监测智慧运营平台 - 共享工具函数 */

// HTML转义防止XSS
function esc(str){return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}

// JS字符串安全序列化（用于onclick属性中的用户数据）
function jsStr(str){return JSON.stringify(String(str))}

// Toast提示（简短）
function showToast(msg,type){
  var t=document.getElementById('toast');
  if(!t){
    t=document.createElement('div');t.id='toast';
    t.style.cssText='position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:99999;padding:10px 24px;border-radius:8px;font-size:14px;transition:opacity .3s';
    document.body.appendChild(t)
  }
  t.textContent=msg;
  t.style.background=type==='error'?'#f5222d':'#333';
  t.style.color='#fff';t.style.opacity='1';t.style.display='block';
  setTimeout(function(){t.style.opacity='0';setTimeout(function(){t.style.display='none'},300)},2500)
}

// 简单防抖
function debounce(fn,delay){
  delay=delay||300;var timer=null;
  return function(){var ctx=this,args=arguments;clearTimeout(timer);timer=setTimeout(function(){fn.apply(ctx,args)},delay)}
}

// 日期格式化 YYYY-MM-DD HH:mm
function formatDate(dateStr){
  if(!dateStr)return '-';
  var d=new Date(dateStr);
  if(isNaN(d.getTime()))return dateStr.substring(0,16);
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')
}

// 相对时间（几分钟前/几小时前）
function relativeTime(dateStr){
  if(!dateStr)return '';
  var now=Date.now(),d=new Date(dateStr).getTime();
  var diff=Math.floor((now-d)/1000);
  if(diff<60)return diff+'秒前';
  if(diff<3600)return Math.floor(diff/60)+'分钟前';
  if(diff<86400)return Math.floor(diff/3600)+'小时前';
  if(diff<2592000)return Math.floor(diff/86400)+'天前';
  return formatDate(dateStr)
}

// 截断字符串
function truncate(str,len){
  len=len||30;
  if(!str||str.length<=len)return str||'';
  return str.substring(0,len)+'...'
}

// 数字格式化（逗号分隔）
function formatNum(n){
  if(n===null||n===undefined)return '-';
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g,',')
}
