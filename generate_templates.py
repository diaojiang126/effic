#!/usr/bin/env python3
# 生成导入模板文件

import os
from openpyxl import Workbook

# 确保模板目录存在
template_dir = os.path.join('static', 'templates')
os.makedirs(template_dir, exist_ok=True)

# 生成设备分类导入模板
def generate_categories_template():
    """生成设备分类导入模板"""
    wb = Workbook()
    ws = wb.active
    ws.title = '设备分类'
    
    # 添加列标题
    ws.append(['分类名称', '描述'])
    
    # 添加示例数据
    ws.append(['服务器', '用于数据处理的服务器'])
    ws.append(['网络设备', '路由器、交换机等网络设备'])
    ws.append(['存储设备', '磁盘阵列、NAS等存储设备'])
    ws.append(['安全设备', '防火墙、入侵检测系统等安全设备'])
    
    # 保存模板文件
    template_file = os.path.join(template_dir, 'categories_import_template.xlsx')
    wb.save(template_file)
    print(f"设备分类模板生成成功: {template_file}")

# 生成设备型号导入模板
def generate_models_template():
    """生成设备型号导入模板"""
    wb = Workbook()
    ws = wb.active
    ws.title = '设备型号'
    
    # 添加列标题
    ws.append(['分类名称', '型号名称', '型号代码', '描述', '规格参数'])
    
    # 添加示例数据
    ws.append(['服务器', '机架式服务器', 'SRV001', '企业级机架式服务器', 'CPU: 2颗至强处理器, 内存: 128GB, 硬盘: 4TB SSD'])
    ws.append(['网络设备', '千兆交换机', 'SWT001', '24口千兆交换机', '端口: 24个千兆电口, 2个万兆光口, 支持VLAN, QoS'])
    ws.append(['存储设备', '网络存储服务器', 'NAS001', '4盘位网络存储服务器', '容量: 4个硬盘位, 支持RAID 0/1/5/10, 网络: 千兆以太网'])
    
    # 保存模板文件
    template_file = os.path.join(template_dir, 'models_import_template.xlsx')
    wb.save(template_file)
    print(f"设备型号模板生成成功: {template_file}")

# 生成设备单元导入模板
def generate_units_template():
    """生成设备单元导入模板"""
    wb = Workbook()
    ws = wb.active
    ws.title = '设备单元'
    
    # 添加列标题
    ws.append(['型号代码', '设备编号', '序列号', '状态', '采购日期', '采购价格', '存放位置', '备注'])
    
    # 添加示例数据
    ws.append(['SRV001', 'SRV-2024-001', 'SN123456', '在库', '2024-01-01', 15000.00, '机房A-1-1', '测试服务器'])
    ws.append(['SRV001', 'SRV-2024-002', 'SN123457', '在库', '2024-01-02', 15000.00, '机房A-1-2', '生产服务器'])
    ws.append(['SWT001', 'SWT-2024-001', 'SN654321', '在库', '2024-01-03', 2500.00, '机房B-2-1', '网络设备'])
    
    # 保存模板文件
    template_file = os.path.join(template_dir, 'units_import_template.xlsx')
    wb.save(template_file)
    print(f"设备单元模板生成成功: {template_file}")

if __name__ == '__main__':
    # 生成所有模板
    generate_categories_template()
    generate_models_template()
    generate_units_template()
    print("所有导入模板生成完成！")
