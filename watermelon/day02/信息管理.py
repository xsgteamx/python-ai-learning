class Customer:
    """客户类"""
    def __init__(self,customer_id,name,gender,phone):
        self.customer_id = customer_id
        self.name = name
        self.gender = gender
        self.phone = phone


    def __str__(self):
        return (f"客户信息:\n"
                f"  ID:{self.customer_id}\n"
                f"  姓名:{self.name}\n"
                f"  性别:{self.gender}\n"
                f"  手机:{self.phone}"

        )


class CustomerManager:
    """客户管理类"""
    def __init__(self):
        self.customers = []
        self.next_id = 1

    def add_customer(self, name, gender,phone):
        if not name or not name.strip():
            print("错误，姓名不能空！")
            return False
        
        if gender  not in ['男','女']:
            print("错误，性别只能为'男','女'!")
            return False
        
        if not phone or len(phone)  != 11 or not phone.isdigit():
            print("错误：手机号必须是11位数字！")
            return False
        for customer in self.customers:
            if customer.phone == phone:
                print(f"错误：手机号 {phone} 已存在！")
                return False
            
    
        customer = Customer(self.next_id,name,gender,phone)
        self.customers.append(customer)
        self.next_id += 1
        print(f"成功添加客户：{customer}")
        return True 

    def display_all_customers(self):
        if not self.customers:
            print("没有用户信息！")
        else:
            for customer in self.customers:
                print(customer)



    def search_customers(self,keyword):
        results = []
        for customer in self.customers:
            if str(customer.customer_id) == keyword or keyword in customer.name or keyword in customer.phone:
                results.append(customer)
                return results

    def delete_customer(self,customer_id):
        for customer in self.customers:
            if customer.customer_id == customer_id:
                self.customers.remove(customer)
                print(f"成功删除客户：{customer}")
                return True
        print(f"错误：未找到ID为 {customer_id} 的客户！")
        return False
    

    def update_customer(self,customer_id,name=None,gender=None,phone=None):
        for customer in self.customers:
            if customer.customer_id == customer_id:
                if name:
                    customer.name = name
            
                if gender:
                    if gender not in ['男', '女']:
                        print("错误：性别只能为'男'或'女'！")
                        return False
                    customer.gender = gender
                if phone:
                    if len(phone) != 11 or not phone.isdigit():
                        print("错误：手机号必须是11位数字！")
                        return False
                    for c in self.customers:
                        if c.customer_id != customer_id and c.phone == phone:
                            print(f"错误：手机号 {phone} 已被其他客户使用！")
                            return False
                    customer.phone = phone
                
                print(f"成功修改客户：{customer}")
                return True
        
        print(f"错误：未找到ID为 {customer_id} 的客户！")
        return False            







def main():
    """主程序 - 交互菜单"""
    manager = CustomerManager()
    
    while True:
        print("\n" + "="*30)
        print("   客户信息管理系统")
        print("="*30)
        print("1. 添加客户")
        print("2. 删除客户")
        print("3. 修改客户")
        print("4. 查询客户")
        print("5. 显示所有客户")
        print("6. 退出系统")
        print("="*30)
        
        choice = input("请选择操作（1-6）：").strip()
        
        if choice == '1':
            # 添加客户（循环直到成功）
            while True:
                name = input("请输入姓名：").strip()
                gender = input("请输入性别（男/女）：").strip()
                phone = input("请输入手机号：").strip()
                
                # 验证姓名
                if not name:
                    print("错误：姓名不能为空，请重新输入！\n")
                    continue
                
                # 验证性别
                if gender not in ['男', '女']:
                    print("错误：性别只能为'男'或'女'，请重新输入！\n")
                    continue
                
                # 验证手机号
                if len(phone) != 11 or not phone.isdigit():
                    print("错误：手机号必须是11位数字，请重新输入！\n")
                    continue
                
                # 全部验证通过，尝试添加
                if manager.add_customer(name, gender, phone):
                    break  # 添加成功，退出循环
                else:
                    print("请重新输入！\n")  # 手机号重复等情况


if __name__ == "__main__":
    main()