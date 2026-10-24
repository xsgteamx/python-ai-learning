
from pathlib import Path


def load_text_files(directory):
    """加载目录下所有 .txt 和 .md 文件。"""
    results = []

    try:
        folder = Path(directory)

        if not folder.is_dir():
            raise FileNotFoundError(f"目录不存在：{directory}")

        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower() in (".txt", ".md"):
                try:
                    content = file.read_text(encoding="utf-8")
                    results.append({
                        "filename": file.name,
                        "content": content,
                        "path": str(file.resolve())
                    })
                except (OSError, UnicodeError) as e:
                    print(f"读取文件失败：{file.name}，原因：{e}")

    except FileNotFoundError:
        print(f"错误：目录不存在，请检查路径：{directory}")
        return []
    except OSError as e:
        print(f"访问目录失败：{e}")
        return []

    return results


# 演示调用
directory = "./docs"
files = load_text_files(directory)

print(f"共加载 {len(files)} 个文件")

for file in files:
    print(f"文件名：{file['filename']}，内容长度：{len(file['content'])} 字符")
