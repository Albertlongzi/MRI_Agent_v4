# MRI AI STUDIO

`apps/web/` 现在是已接入的正式静态前端，根文件已由 `mri_ai_studio` 的可部署版本替换。

前端源码快照仍保留在：

- [mri_ai_studio/README.md](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/mri_ai_studio/README.md)
- [mri_ai_studio/apps/web/index.html](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/mri_ai_studio/apps/web/index.html)
- [mri_ai_studio/apps/web/styles.css](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/mri_ai_studio/apps/web/styles.css)
- [mri_ai_studio/apps/web/app.js](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/mri_ai_studio/apps/web/app.js)

当前部署方式：

- FastAPI 继续挂载 `apps/web` 到 `/static`
- `/` 返回 [index.html](/home/longz2/common/medgemma/MRI_Agent_v4/apps/web/index.html)
- 页面资源从 `/static/styles.css` 和 `/static/app.js` 加载

当前 UI 已接入这些后端路径：

- `/api/health`
- `/api/planner/health`
- `/api/session`
- `/api/graph`
- `/api/events`
- `/api/chat`
- `/api/patch`
- `/api/proposals/apply-latest`
- `/api/execute/next`
- `/api/execute/until-done`
- `/api/reset`
- `/api/tools`
- `/api/domains`
- `/api/capabilities`
- `/api/tools/bridge/health`
- `/artifacts/...`

本地静态预览：

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4/apps/web
./serve.sh
```

同源联调：

```bash
cd /home/longz2/common/medgemma/MRI_Agent_v4
PYTHONPATH=/home/longz2/common/medgemma/MRI_Agent_v4 ./.venv/bin/python run_demo.py
```

然后打开：

- `http://127.0.0.1:8008/`
