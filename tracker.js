/* 加在網站 </body> 前：<script src="http://你的後端網址/tracker.js"></script>
   自動記錄瀏覽，並記錄按鈕與連結的點擊（可用 data-track="名稱" 自訂）。 */
(function(){var S;try{S=localStorage.getItem("hy_s");if(!S){S=Math.random().toString(36).slice(2);localStorage.setItem("hy_s",S)}}catch(e){S="anon"}
var API=((document.currentScript&&document.currentScript.src)||"").replace(/\/tracker\.js.*$/,"");
function send(t,target){try{var b=JSON.stringify({type:t,path:location.pathname+location.hash,target:target||"",session:S});
if(navigator.sendBeacon)navigator.sendBeacon(API+"/api/track",new Blob([b],{type:"text/plain"}));else fetch(API+"/api/track",{method:"POST",body:b,keepalive:true})}catch(e){}}
send("pageview");
document.addEventListener("click",function(e){var n=e.target.closest("a,button,[data-track]");if(!n)return;
send("click",(n.getAttribute("data-track")||n.id||n.textContent||"").replace(/\s+/g," ").trim().slice(0,40))});
})();
