import asyncio
import time

async def boil_water():
    print("  [任务1] 开始烧水...")
    await asyncio.sleep(3)  # 模拟耗时3秒的I/O操作
    print("  [任务1] 水烧开了！")
    return "热水"

async def make_coffee():
    print("  [任务2] 开始煮咖啡...")
    await asyncio.sleep(2)  # 模拟耗时2秒的I/O操作
    print("  [任务2] 咖啡煮好了！")
    return "咖啡"

async def main():
    # 创建两个"任务"，让事件循环去调度它们
    task1 = asyncio.create_task(boil_water())
    task2 = asyncio.create_task(make_coffee())
    
    print("任务已创建，等待完成...")
    
    # 等待两个任务都完成
    result1 = await task1
    result2 = await task2
    
    print(f"最终结果: {result1} 和 {result2}")

if __name__ == "__main__":
    start = time.time()
    # 运行主协程，直到完成
    asyncio.run(main())
    end = time.time()
    print(f"总耗时: {end - start:.2f} 秒")