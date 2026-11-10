(() => {
  const root=document.getElementById('lineIdentityApp');
  if(!root) return;
  const status=document.getElementById('lineIdentityStatus'), error=document.getElementById('lineIdentityError');
  const choice=document.getElementById('lineIdentityChoice'), known=document.getElementById('lineIdentityKnown');
  const select=document.getElementById('lineIdentityName'), save=document.getElementById('lineIdentitySave');
  const continueButton=document.getElementById('lineIdentityContinue');
  let token=null, current=null;
  let schedule=false, mine=false;
  const next=staff => schedule ? '/staff/schedule'+(mine?'?mine=1':'') : '/kibo?'+new URLSearchParams({staff_id:String(staff.id),week:root.dataset.week});
  async function api(path,extra={}) {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({id_token:token,...extra})});
    if(!response.ok){
      const data=await response.json().catch(()=>null);
      throw new Error(data?.error || 'Không xác nhận được tài khoản. Vui lòng mở lại từ LINE.');
    }
    return response.json();
  }
  function showChoice(){known.hidden=true;choice.hidden=false;status.textContent='Xác nhận tên của bạn một lần để hệ thống ghi nhớ.';}
  continueButton.onclick=()=>{if(current) location.assign(next(current));};
  document.getElementById('lineIdentityChange').onclick=showChoice;
  save.onclick=async()=>{
    if(!select.value){error.textContent='Vui lòng chọn tên của bạn.';return;}
    save.disabled=true;error.textContent='';
    try{
      const data=await api('/line/identity/link',{staff_id:select.value});
      token=null;location.assign(next(data.linked));
    }catch(problem){error.textContent=problem.message;save.disabled=false;}
  };
  (async()=>{
    try{
      await liff.init({liffId:root.dataset.liffId});
      const params=new URL(location.href).searchParams;
      schedule=params.get('view')==='schedule'; mine=params.get('mine')==='1';
      if(schedule){document.querySelector('h1').textContent='Xem lịch làm việc';save.textContent='Xác nhận tên & xem lịch';continueButton.textContent='Mở lịch làm việc';}
      if(!liff.isLoggedIn()){
        status.textContent='Đăng nhập LINE để nhớ tên của bạn.';
        const login=document.createElement('button');login.type='button';login.textContent='Đăng nhập bằng LINE';
        login.onclick=()=>liff.login({redirectUri:location.origin+'/kibo/line?'+new URLSearchParams({week:root.dataset.week,...(schedule?{view:'schedule',mine:mine?'1':'0'}:{})})});
        root.prepend(login);return;
      }
      token=liff.getIDToken();
      if(!token)throw new Error('Cần cho phép LINE xác nhận tài khoản (openid). Hãy mở lại từ LINE.');
      const data=await api('/line/identity');
      for(const person of data.staff){
        const option=document.createElement('option');option.value=person.id;
        option.textContent=person.name+(person.taken?' · Đã liên kết':'');option.disabled=person.taken;
        select.append(option);
      }
      if(data.linked){
        current=data.linked;select.value=current.id;
        if(new URL(location.href).searchParams.get('change')==='1'){showChoice();return;}
        document.getElementById('lineIdentityKnownName').textContent=current.name;
        status.textContent='Đã nhận đúng tên của bạn.';known.hidden=false;
        // Open the form automatically after a short, cancellable confirmation.
        let cancelled=false;
        document.getElementById('lineIdentityChange').addEventListener('click',()=>{cancelled=true;});
        setTimeout(()=>{if(!cancelled)location.assign(next(current));},1200);
      }else showChoice();
    }catch(problem){status.textContent='Chưa thể kết nối LINE.';error.textContent=problem.message;}
  })();
})();
