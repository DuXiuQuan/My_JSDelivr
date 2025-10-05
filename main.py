# main.py
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

app = FastAPI()

# 配置
NPM_REGISTRY = "https://registry.npmjs.org"
JSDELIVR_CDN = "https://cdn.jsdelivr.net/npm"
UNPKG_CDN = "https://unpkg.com"


def resolve_entry_file(package_json: dict) -> str:
    """
    解析包的入口文件路径
    按照优先级：jsdelivr > exports["."]["default"] > exports["."]字符串 > main
    如果都不存在则返回 None
    """
    # 1. 检查 jsdelivr 字段
    if "jsdelivr" in package_json:
        entry = package_json["jsdelivr"]
        return entry.lstrip("./")

    # 2. 检查 exports["."]
    if "exports" in package_json:
        exports = package_json["exports"]

        if isinstance(exports, dict) and "." in exports:
            dot_export = exports["."]

            # 2.1 如果 exports["."] 是对象，检查 "default" 字段
            if isinstance(dot_export, dict):
                if "default" in dot_export:
                    entry = dot_export["default"]
                    return entry.lstrip("./")
                # 如果是对象但没有 default，继续检查 main

            # 2.2 如果 exports["."] 是字符串，直接使用
            elif isinstance(dot_export, str):
                return dot_export.lstrip("./")

    # 3. 回退到 main 字段
    if "main" in package_json:
        entry = package_json["main"]
        return entry.lstrip("./")

    # 4. 所有条件都不满足，返回 None
    return None


@app.get("/")
async def root():
    """根路径返回简单的 HTML 页面"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>jsDelivr CDN</title>
    </head>
    <body>
        <h1>jsDelivr CDN API</h1>
        <ul>
            <li>获取包入口文件: /{package_name}</li>
            <li>获取包入口文件（指定版本）: /{package_name}@{version}</li>
            <li>浏览目录（最新版）: /{package_name}/</li>
            <li>浏览目录（指定版本）: /{package_name}@{version}/</li>
            <li>获取文件: /{package_name}@{version}/{file_path}</li>
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/{full_path:path}")
async def handle_request(full_path: str, request: Request):
    """统一处理所有请求"""

    # 情况1: 包名（不含@符号和/） - 例如 /react
    if "@" not in full_path and not full_path.endswith("/"):
        return await get_package_entry(full_path, None)

    # 情况2: 包名/ （不含@符号，以/结尾） - 例如 /vue/
    if "@" not in full_path and full_path.endswith("/"):
        package_name = full_path.rstrip("/")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{NPM_REGISTRY}/{package_name}")

                if response.status_code == 404:
                    return HTMLResponse(
                        content="<h1>404 Package Not Found</h1>",
                        status_code=404
                    )

                package_data = response.json()
                latest_version = package_data.get("dist-tags", {}).get("latest")

                if latest_version:
                    return RedirectResponse(url=f"/{package_name}@{latest_version}/")
                else:
                    return HTMLResponse(
                        content="<h1>Error: Cannot find latest version</h1>",
                        status_code=500
                    )

            except Exception as e:
                return HTMLResponse(
                    content=f"<h1>Error: {str(e)}</h1>",
                    status_code=500
                )

    # 情况3: 包名@版本（不以/结尾） - 例如 /vue@3.3.4
    if "@" in full_path and "/" not in full_path.split("@", 1)[1]:
        parts = full_path.split("@", 1)
        package_name = parts[0]
        version = parts[1]
        return await get_package_entry(package_name, version)

    # 情况4: 包名@版本/ - 例如 /vue@3.3.4/
    if full_path.endswith("/"):
        parts = full_path.rstrip("/").split("@", 1)
        if len(parts) == 2:
            package_name = parts[0]
            version = parts[1]
            return await get_package_directory(package_name, version)

    # 情况5: 包名@版本/文件路径 - 例如 /vue@3.3.4/dist/vue.runtime.esm-browser.js
    if "@" in full_path:
        parts = full_path.split("@", 1)
        package_name = parts[0]

        if "/" in parts[1]:
            version_and_path = parts[1].split("/", 1)
            version = version_and_path[0]
            file_path = version_and_path[1]
            return await get_package_file(package_name, version, file_path)

    return Response(content="404 Not Found", status_code=404)


async def get_package_entry(package_name: str, version: str = None):
    """获取包的入口文件内容"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{NPM_REGISTRY}/{package_name}")

            if response.status_code == 404:
                return Response(
                    content="404 Package Not Found",
                    status_code=404
                )

            package_data = response.json()

            # 如果没有指定版本，使用最新版本
            if not version:
                version = package_data.get("dist-tags", {}).get("latest")

            # 检查版本是否存在
            if version not in package_data.get("versions", {}):
                return Response(
                    content=f"404 Version {version} Not Found",
                    status_code=404
                )

            # 获取该版本的 package.json
            version_data = package_data["versions"][version]

            # 解析入口文件路径
            entry_file = resolve_entry_file(version_data)

            # 如果无法解析入口文件，返回 404
            if entry_file is None:
                return Response(
                    content="404 Not Found",
                    status_code=404
                )

            # 获取入口文件内容
            return await get_package_file(package_name, version, entry_file)

        except Exception as e:
            return Response(
                content=f"Error: {str(e)}",
                status_code=500
            )


async def get_package_directory(package_name: str, version: str):
    """返回包的目录列表页面"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{NPM_REGISTRY}/{package_name}")

            if response.status_code == 404:
                return HTMLResponse(
                    content="<h1>404 Package Not Found</h1>",
                    status_code=404
                )

            package_data = response.json()

            if version not in package_data.get("versions", {}):
                return HTMLResponse(
                    content=f"<h1>404 Version {version} Not Found</h1>",
                    status_code=404
                )

            # 简单的文件列表
            files = ["index.js", "package.json", "README.md"]

            file_links = ""
            for file_name in files:
                file_links += f'<li><a href="{file_name}">{file_name}</a></li>\n'

            html_content = f"""<!DOCTYPE HTML>
<html>
<head>
<meta charset="utf-8">
<title>{package_name}</title>
</head>
<body>
<h1>{package_name}</h1>
<hr>
<ul>
{file_links}</ul>
<hr>
</body>
</html>"""

            return HTMLResponse(content=html_content)

        except Exception as e:
            return HTMLResponse(
                content=f"<h1>Error: {str(e)}</h1>",
                status_code=500
            )


async def get_package_file(package_name: str, version: str, file_path: str):
    """获取指定版本包的具体文件"""
    cdn_urls = [
        f"{JSDELIVR_CDN}/{package_name}@{version}/{file_path}",
        f"{UNPKG_CDN}/{package_name}@{version}/{file_path}",
    ]

    transport = httpx.AsyncHTTPTransport(retries=2)
    async with httpx.AsyncClient(
            timeout=15.0,
            transport=transport,
            follow_redirects=True
    ) as client:

        for cdn_url in cdn_urls:
            try:
                response = await client.get(cdn_url)

                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "text/plain")

                    return Response(
                        content=response.content,
                        media_type=content_type,
                        headers={
                            "Content-Type": content_type,
                            "Access-Control-Allow-Origin": "*"
                        }
                    )

            except Exception:
                continue

        return Response(
            content="404 File Not Found",
            status_code=404
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
