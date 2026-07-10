
# 变量

# 程序中可以改变的量 python 是弱类型的语言 但并不是没有类型
#语法 : 变量名=变量值
a = 10

var = 10
print(var)

num1 = 10
num2 = 20
num3 = num1 + num2

print(num3)

name = "xuesheng" #字符串

age  = 18 #整数

height = 178.5 #浮点数

print('-----')
#给多个变量进行赋值
var1 = var2 = var3 = 10
# woc? 还能这么玩么?
# 多个变量的值不一样
var4,var5,var6 = 10,20,30

print(var4)
print(var5)
print(var6)


print('-----')

# 标识符命名规则
"""
* only 字母 数字 下滑线 
* 禁止数字开头
* Name != name
* 禁止和关键字重名
"""
studentName = "xuesheng"
StudentName = "xuesheng"
student_name = "xuesheng"


print('----------------')

a = 10
b = 20
a,b = b,a
print(a,b)

print('----------------')

# 常亮

PI=3.14
print(pi)
