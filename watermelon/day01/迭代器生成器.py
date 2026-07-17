#迭代器
#迭代器是 Python 中用来逐个访问容器中元素的对象，它让你可以"记住"当前访问到了哪个位置，并且每次只能往前移动，不能后退。



my_list = [1, 2, 3, 4, 5]
my_iterator = iter(my_list)
for i in range(6):
    try:
        item = next(my_iterator)
        print(item)
    except StopIteration:
        print("错误：没有元素了")
        break







#简单倒计时迭代器
class Countdown:
    def __init__(self,start):
        self.current = start

    def __iter__(self):
        return self
    
    def __next__(self):
        if self.current <= 0:
            raise StopIteration
        value = self.current
        self.current -= 1
        return value
    


Countdown = Countdown(5)
for num in Countdown:
    print(num)












#生成器
#生成器是 Python 中创建迭代器的最简单方式。它用 yield 关键字代替 return，可以记住当前执行状态，下次调用时从上次暂停的地方继续执行。
def generator_function():
    print("开始执行")
    yield 1
    print("继续执行")
    yield 2
    print("结束")

gen = generator_function()  # 创建生成器对象
print(next(gen))
print(next(gen))
print(next(gen))




















#                  生成器 vs 迭代器 vs 列表
# 特性	     列表	           迭代器	        生成器
# 创建方式	[1,2,3]	         iter(list)	      yield 函数
# 存储方式	全部加载到内存	    按需生成    	按需生成
# 遍历次数	多次	             一次	         一次
# 是否可索引 ✅ 是 (list[0])	  ❌ 否	         ❌ 否
# 内存占用	  大	              小	         最小
# 使用场景	小数据集	        大数据集	大数据集、流式处理
