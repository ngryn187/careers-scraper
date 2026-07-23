@echo off
cd /d C:\Users\tyson\Desktop
git config user.email "ngrynai@gmail.com"
git config user.name "ngryn187"
git add -A
git diff --cached --quiet && echo Nothing to commit. || git commit -m "update"
git push origin HEAD:main
echo Done!
pause
