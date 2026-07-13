# #鸟基本类
# class Birds:
#     def __init__(self,name,color,skill_description):
#         self.name = name
#         self.color = color
#         self.skill_description = skill_description

#     def fly(self):
#         print(f"{self.name}正在飞行...")

#     def call(self):
#         print(f"{self.name}正在发出叫声...")
    
#     def use_skill(self):
#         print(f"{self.name}使用了技能：{self.skill_description}")

# #红色鸟子类
# class RedBirds(Birds):
#     def __init__(self):
#         super().__init__("红火","红色","撞击前方障碍物，造成大量伤害")

#     def fly(self):
#         print("红火以稳定的速度向前飞行...")
#     def call(self):
#         print("红火发出 'wei呀....' 的叫声")


# #黄色鸟子类
# class YellowBirds(Birds):
#     def __init__(self):
#         super().__init__("小黄", "黄色", "瞬间加速，穿透薄障碍物")

#     def fly(self):
#         print("小黄快速向前飞行...")

#     def call(self):
#         print("小黄发出 '啾啾啾....' 的叫声")

# #蓝鸟子类
# class BlueBirds(Birds):
#     def __init__(self):
#         super().__init__("小蓝", "蓝色", "分裂成三只小鸟，分散攻击")

#     def fly(self):
#         print("小蓝优雅地向前飞行...")
#     def call(self):
#         print("小蓝发出 '叽叽叽....' 的叫声")



# #障碍物类
# class Obstacle:
#     def __init__(self,name,strength):
#         self.name = name
#         self.strength = strength

#     def be_attacked(self,bird):
#         print(f"{bird.name}冲向了{self.name}")
#         if isinstance(bird,RedBirds):
#             damage = 80
#         elif isinstance(bird,YellowBirds):
#             damage = 50
#         elif isinstance(bird,BlueBirds):
#             damage =30 *3
#         self.strength -=damage
#         if self.strength <= 0:
#             print(f"{self.name} 被摧毁了！")
#         else:
#             print(f"{self.name} 还剩余 {self.strength} 点强度")
   
# if __name__ == "__main__":
#     red_bird = RedBirds()
#     yellow_bird = YellowBirds()
#     blue_bird = BlueBirds()

#     Obstacle1 = Obstacle("木头堡垒", 100)
#     Obstacle2 = Obstacle("石头塔楼", 200)


# Obstacle1.be_attacked(red_bird)
# Obstacle2.be_attacked(yellow_bird)
# Obstacle2.be_attacked(blue_bird)










class Birds:
    def __init__(self, name, color, skill_description, attack_power):
        self.name = name
        self.color = color
        self.skill_description = skill_description
        self.attack_power = attack_power  # 新增攻击力属性

    def fly(self):
        print(f"{self.name}正在飞行...")
    
    def call(self):
        print(f"{self.name}正在发出叫声...")
    
    def use_skill(self):
        print(f"{self.name}使用了技能：{self.skill_description}")

class RedBirds(Birds):
    def __init__(self):
        super().__init__("红火", "红色", "撞击前方障碍物，造成大量伤害", 80)
        

    def fly(self):
        print("红火以稳定的速度向前飞行...")
    
    def call(self):
        print("红火发出 'wei呀....' 的叫声")

class YellowBirds(Birds):
    def __init__(self):
        super().__init__("小黄", "黄色", "瞬间加速，穿透薄障碍物", 120)

    def fly(self):
        print("小黄快速向前飞行...")

    def call(self):
        print("小黄发出 '啾啾啾....' 的叫声")

class BlueBirds(Birds):
    def __init__(self):
        super().__init__("小蓝", "蓝色", "分裂成三只小鸟，分散攻击", 90)

    def fly(self):
        print("小蓝优雅地向前飞行...")
    
    def call(self):
        print("小蓝发出 '叽叽叽....' 的叫声")

class Obstacle:
    def __init__(self, name, strength):
        self.name = name
        self.strength = strength

    def be_attacked(self, bird):
        print(f"{bird.name}冲向了{self.name}")
        damage = bird.attack_power
        self.strength -= damage
        bird.use_skill()
        
        if self.strength <= 0:
            print(f"{self.name} 被摧毁了！")
        else:
            print(f"{self.name} 还剩余 {self.strength} 点强度")



if __name__ == "__main__":
    red_bird = RedBirds()
    yellow_bird = YellowBirds()
    blue_bird = BlueBirds()

    Obstacle1 = Obstacle("木头堡垒", 100)
    Obstacle2 = Obstacle("石头塔楼", 200)


Obstacle1.be_attacked(red_bird)
Obstacle2.be_attacked(yellow_bird)
Obstacle2.be_attacked(blue_bird)