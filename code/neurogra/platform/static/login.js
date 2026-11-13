'use strict';
const $=id=>document.getElementById(id);
let csrf='';
async function bootstrap(){
  const response=await fetch('/api/session',{cache:'no-store'});
  if(!response.ok)throw new Error('无法连接服务，请刷新重试。');
  const session=await response.json();csrf=session.token;
  if(session.authenticated)location.replace('/chat');
}
function error(message){$('login-error').textContent=message;$('login-error').hidden=!message;}
$('toggle-password').addEventListener('click',()=>{const visible=$('password').type==='password';$('password').type=visible?'text':'password';$('toggle-password').textContent=visible?'隐藏':'显示';$('toggle-password').setAttribute('aria-label',visible?'隐藏密码':'显示密码');});
$('login-form').addEventListener('submit',async event=>{
  event.preventDefault();error('');$('login-submit').disabled=true;$('login-submit').textContent='正在登录…';
  try{
    if(!csrf)await bootstrap();
    const response=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':csrf},body:JSON.stringify({account:$('account').value.trim(),password:$('password').value})});
    const data=await response.json();
    if(!response.ok)throw new Error(data.error||'登录失败，请重试。');
    $('password').value='';location.replace('/chat');
  }catch(exception){error(exception.message||'无法连接服务，请重试。');}
  finally{$('login-submit').disabled=false;$('login-submit').textContent='登录';}
});
bootstrap().catch(exception=>error(exception.message));
