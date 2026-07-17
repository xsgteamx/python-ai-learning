#闭包
#即使外层函数执行完了，内层函数还能记住并使用外层函数的变量。

def counter():
    count = 0
    
    def add():
        nonlocal count
        count += 1
        return count
    
    return add

c1 = counter()
c2 = counter()

print(c1())
print(c1())
print(c1())

print(c2())
print(c2())
print(c2())



#装饰器
#给函数"套壳子"，在原函数执行前后自动加代码，而不修改原函数本身。

def my_decorator(func):
    def wrapper():
        print("马甲：执行前")
        func()
        print("马甲：执行后")
    return wrapper

# 使用装饰器
@my_decorator
def say_hello():
    print("Hello!")

say_hello()