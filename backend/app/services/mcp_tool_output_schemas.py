"""TASK-099 每个查询工具的数据输出 JSON Schema。

内部 DSL：s=string、i=integer、b=boolean、z=null；o/a/u 分别表示 object、
array、anyOf。该快照来自 175 个已注册查询工具的非空隔离运行样例；新增字段
必须更新 schema，并由运行时样例校验覆盖。
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.services.query_projection import (
    _APPROVAL_DETAIL_FIELDS,
    _APPROVAL_DETAIL_MONEY_FIELDS,
)


_COMPACT_SCHEMAS_JSON = r"""
{
  "erp_search_purchase_plans": {"a":{"o":{"audit_status":"s","created_at":"s","id":"i","plan_date":"z","plan_no":"s","status":"s","supplier_id":"i","supplier_name":"s","total_amount":"s","total_qty":"s","updated_at":"s"}}},
  "erp_preview_purchase_needs": {"a":{"o":{"current_stock":"s","estimated_amount":"s","in_progress_qty":"s","in_transit_stock":"s","product_code":"s","product_id":"i","product_name":"s","purchase_price":"s","sales_20d":"s","sku_id":"i","spec_code":"s","spec_name":"s","suggested_qty":"s","supplier_id":"i"}}},
  "erp_get_purchase_plan": {"o":{"audit_status":"s","created_at":"s","id":"i","plan_date":"z","plan_no":"s","status":"s","supplier_id":"i","supplier_name":"s","total_amount":"s","total_qty":"s","updated_at":"s"}},
  "erp_list_purchase_plan_items": {"a":{"o":{"amount":{"u":["s","z"]},"id":"i","planned_qty":"s","product_code":"s","product_id":"i","product_name":"s","purchase_price":{"u":["s","z"]},"purchased_qty":"s","remaining_qty":"s","sku_id":"z","spec_code":"z","spec_name":"z"}}},
  "erp_get_purchase_plan_creation_request": {"o":{"plan_id":{"u":["i","z"]},"status":"s"}},
  "erp_search_purchase_orders": {"a":{"o":{"arrival_status":"s","arrived_qty":"s","audit_status":"s","created_at":"s","difference_qty":"s","id":"i","order_no":"s","prepay_amount":"s","status":"s","total_amount":"s","total_qty":"s","updated_at":"s"}}},
  "erp_get_purchase_order": {"o":{"arrival_status":"s","arrived_qty":"s","audit_status":"s","created_at":"s","difference_qty":"s","id":"i","order_no":"s","prepay_amount":"s","status":"s","total_amount":"s","total_qty":"s","updated_at":"s"}},
  "erp_list_purchase_order_items": {"a":{"o":{"amount":"s","arrived_qty":"s","difference_qty":"s","id":"i","in_transit_qty":"s","product_code":"s","product_id":"i","product_name":"s","purchase_price":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_order_list_orders": {"a":{"o":{"audit_status":"s","flags":{"a":"s"},"flags_label":{"a":"s"},"freight":"s","id":"i","logistics_id":{"u":["i","z"]},"logistics_name":{"u":["s","z"]},"order_amount":"s","order_no":"s","order_time":"s","paid_amount":"s","pay_status":"s","pay_time":"z","platform_discount":"s","platform_name":"s","seller_discount":"s","ship_time":"z","shop_id":"i","shop_name":"s","status":"s","status_label":"s","total_qty":"s","warehouse_id":"i","warehouse_name":"s","weight":"z"}}},
  "erp_order_get_order": {"o":{"audit_status":"s","flags":{"a":"s"},"flags_label":{"a":"s"},"freight":"s","id":"i","logistics_id":"i","logistics_name":"s","order_amount":"s","order_no":"s","order_time":"s","paid_amount":"s","pay_status":"s","pay_time":"z","platform_discount":"s","platform_name":"s","seller_discount":"s","ship_time":"z","shop_id":"i","shop_name":"s","status":"s","status_label":"s","total_qty":"s","warehouse_id":"i","warehouse_name":"s","weight":"z"}},
  "erp_order_list_order_items": {"a":{"o":{"amount":"s","id":"i","is_gift":"b","order_id":"i","price":"s","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"z","sku_name":"z"}}},
  "erp_order_list_order_logs": {"a":{"o":{"action":"s","created_at":"s","event_id":"i","source":"s","status":{"u":["s","z"]},"step":{"u":["i","z"]}}}},
  "erp_ship_list_shipments": {"a":{"o":{"actual_weight_g":"z","created_at":"s","expected_weight_g":"z","id":"i","order_id":"i","order_no":"s","package_no":"i","platform_sync_status":"s","shipment_no":"s","shipped_at":"z","shipping_type":"s","source":"s","status":"s","tracking_no_masked":"s","updated_at":"s","warehouse_id":"i","weight_out_of_range":"b","weight_overridden":"b"}}},
  "erp_ship_list_order_shipments": {"a":{"o":{"actual_weight_g":"z","created_at":"s","expected_weight_g":"z","id":"i","order_id":"i","order_no":"s","package_no":"i","platform_sync_status":"s","shipment_no":"s","shipped_at":"z","shipping_type":"s","source":"s","status":"s","tracking_no_masked":"s","updated_at":"s","warehouse_id":"i","weight_out_of_range":"b","weight_overridden":"b"}}},
  "erp_ship_get_shipment": {"o":{"actual_weight_g":"z","created_at":"s","expected_weight_g":"z","id":"i","order_id":"i","order_no":"s","package_no":"i","platform_sync_status":"s","shipment_no":"s","shipped_at":"z","shipping_type":"s","source":"s","status":"s","tracking_no_masked":"s","updated_at":"s","warehouse_id":"i","weight_out_of_range":"b","weight_overridden":"b"}},
  "erp_ship_list_shipment_items": {"a":{"o":{"id":"i","order_item_id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_order_list_order_flags": {"a":{"o":{"code":"s","label":"s"}}},
  "erp_search_purchase_returns": {"a":{"o":{"audit_status":"s","auto_out":"b","created_at":"s","id":"i","order_id":"z","order_no":"z","refund_amount":"s","refund_status":"s","return_no":"s","returned_qty":"s","status":"s","supplier_id":"i","supplier_name":"s","total_qty":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_get_purchase_return": {"o":{"audit_status":"s","auto_out":"b","created_at":"s","id":"i","order_id":"z","order_no":"z","refund_amount":"s","refund_status":"s","return_no":"s","returned_qty":"s","status":"s","supplier_id":"i","supplier_name":"s","total_qty":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_list_purchase_return_items": {"a":{"o":{"amount":"s","art_no":"z","id":"i","product_code":"s","product_id":"i","product_name":"s","return_qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","unit_id":"z","unit_name":"z","unit_price":"s"}}},
  "erp_get_purchase_return_stock_out": {"u":[{"o":{"audit_status":"s","audit_time":"z","created_at":"s","id":"i","off_shelf_status":"s","status":"s","stock_out_no":"s","stock_out_type":"s","total_qty":"s","warehouse_id":"i"}},"z"]},
  "erp_get_purchase_return_refund": {"u":[{"o":{"amount":"s","audit_status":"s","created_at":"s","id":"i","income_no":"s","income_type":"s","status":"s","supplier_id":"i"}},"z"]},
  "erp_search_products": {"a":{"o":{"art_no":"z","barcode":{"u":["s","z"]},"brand_id":"z","brand_name":"z","category_id":"z","category_name":"z","code":"s","id":"i","is_disabled":"b","is_gift":"b","listed":"b","listed_at":"z","main_unit_id":"z","measure_type":"s","name":"s","unit_group_id":"z","unit_name":"z"}}},
  "erp_get_product": {"o":{"art_no":"z","barcode":{"u":["s","z"]},"brand_id":"z","brand_name":"z","category_id":"z","category_name":"z","code":"s","id":"i","is_disabled":"b","is_gift":"b","listed":"b","listed_at":"z","main_unit_id":"z","measure_type":"s","name":"s","unit_group_id":"z","unit_name":"z"}},
  "erp_list_product_skus": {"a":{"o":{"barcode":"s","id":"i","is_active":"b","product_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_search_combos": {"a":{"o":{"combo_no":"s","id":"i","is_enabled":"b","main_product_id":"i","main_product_name":"s","main_sku_id":"i","main_sku_spec_code":"s","name":"s"}}},
  "erp_get_combo": {"o":{"combo_no":"s","id":"i","is_enabled":"b","main_product_id":"i","main_product_name":"s","main_sku_id":"i","main_sku_spec_code":"s","name":"s"}},
  "erp_list_combo_items": {"a":{"o":{"combo_id":"i","id":"i","product_id":"i","product_name":"s","qty":"s","sku_id":{"u":["i","z"]},"sku_spec_code":{"u":["s","z"]},"sort_order":"i"}}},
  "erp_product_expand_combo": {"a":{"o":{"combo_id":{"u":["i","z"]},"kind":"s","product_id":"i","product_name":"s","qty":"s","sku_id":{"u":["i","z"]},"sku_spec_code":{"u":["s","z"]},"sort_order":{"u":["i","z"]}}}},
  "erp_company_get_company": {"o":{"full_name":"s","id":"i","short_name":{"u":["s","z"]}}},
  "erp_company_list_departments": {"a":{"o":{"id":"i","name":"s","parent_id":"z","sort_order":"i"}}},
  "erp_company_get_department": {"o":{"id":"i","name":"s","parent_id":"z","sort_order":"i"}},
  "erp_company_list_positions": {"a":{"o":{"id":"i","name":"s"}}},
  "erp_company_get_position": {"o":{"id":"i","name":"s"}},
  "erp_company_list_staff": {"a":{"o":{"department_id":{"u":["i","z"]},"department_name":{"u":["s","z"]},"id":"i","is_active":"b","name":"s","position_id":{"u":["i","z"]},"position_name":{"u":["s","z"]},"staff_no":{"u":["s","z"]}}}},
  "erp_company_get_staff": {"o":{"department_id":"i","department_name":"s","id":"i","is_active":"b","name":"s","position_id":"i","position_name":"s","staff_no":"s"}},
  "erp_company_get_staff_data_scope": {"o":{"scope_type":"s","staff_id":"i"}},
  "erp_assistant_capabilities": {"o":{"features":{"a":{"o":{"code":"s","document_types":{"a":"s"},"enabled":"b","reason":"s"}}},"modules":{"a":"s"},"provider_mode":"s","service_status":"s"}},
  "erp_product_list_categories": {"a":{"o":{"id":"i","name":"s","parent_id":"z","sort_order":"i"}}},
  "erp_product_list_brands": {"a":{"o":{"id":"i","name":"s"}}},
  "erp_product_list_units": {"a":{"o":{"code":{"u":["s","z"]},"id":"i","name":"s"}}},
  "erp_product_list_unit_groups": {"a":{"o":{"id":"i","item_count":"i","main_unit_id":"i","main_unit_name":"s","name":"s"}}},
  "erp_product_list_unit_group_items": {"a":{"o":{"conversion_rate":"s","id":"i","unit_group_id":"i","unit_id":"i","unit_name":"s"}}},
  "erp_product_list_spec_types": {"a":{"o":{"category_id":{"u":["i","z"]},"category_name":{"u":["s","z"]},"id":"i","name":"s"}}},
  "erp_product_list_spec_values": {"a":{"o":{"code":{"u":["s","z"]},"id":"i","name":"s","sort_order":"i","spec_type_id":"i"}}},
  "erp_search_suppliers": {"a":{"o":{"address":{"u":["s","z"]},"code":"s","id":"i","is_active":"b","name":"s","phone":{"u":["s","z"]}}}},
  "erp_get_supplier": {"o":{"address":{"u":["s","z"]},"code":"s","id":"i","is_active":"b","name":"s","phone":{"u":["s","z"]}}},
  "erp_list_supplier_accounts": {"a":{"o":{"account_no":{"u":["s","z"]},"balance":"s","bank":{"u":["s","z"]},"bank_no":{"u":["s","z"]},"branch":{"u":["s","z"]},"customer_id":"z","id":"i","is_default":"b","supplier_id":"i"}}},
  "erp_list_customer_accounts": {"a":{"o":{"account_no":{"u":["s","z"]},"balance":"s","bank":{"u":["s","z"]},"bank_no":{"u":["s","z"]},"branch":{"u":["s","z"]},"customer_id":"i","id":"i","is_default":"b","supplier_id":"z"}}},
  "erp_list_supplier_prices": {"a":{"o":{"id":"i","price":"s","product_code":"s","product_id":"i","product_name":"s","product_status":"s","sku_id":"z","sku_spec_code":"z","status":"s","supplier_id":"i"}}},
  "erp_search_customers": {"a":{"o":{"address":"z","code":"s","id":"i","is_active":"b","name":"s","phone":"z"}}},
  "erp_get_customer": {"o":{"address":"s","code":"s","id":"i","is_active":"b","name":"s","phone":"s"}},
  "erp_search_after_sale_returns": {"a":{"o":{"audit_time":"z","buyer_phone":"s","created_at":"s","freight":"z","freight_refunded":"b","goods_status":"z","id":"i","is_arrived":"b","is_rejected_sign":"b","logistics_company":{"u":["s","z"]},"need_return_goods":"b","order_id":"i","order_no":"s","order_status":"s","refund_amount":"s","return_no":"s","return_qty":"s","shop_id":"i","shop_name":"s","status":"s","status_label":"s","stock_in_id":"z","stock_in_no":"z","stock_in_status":"z","tracking_no":{"u":["s","z"]},"updated_at":"s","warehouse_id":"z","warehouse_name":"z"}}},
  "erp_get_after_sale_return": {"o":{"audit_time":"z","buyer_phone":"s","created_at":"s","freight":"z","freight_refunded":"b","goods_status":"z","id":"i","is_arrived":"b","is_rejected_sign":"b","logistics_company":"s","need_return_goods":"b","order_id":"i","order_no":"s","order_status":"s","refund_amount":"s","return_no":"s","return_qty":"s","shop_id":"i","shop_name":"s","status":"s","status_label":"s","stock_in_id":"z","stock_in_no":"z","stock_in_status":"z","tracking_no":"s","updated_at":"s","warehouse_id":"z","warehouse_name":"z"}},
  "erp_list_after_sale_return_items": {"a":{"o":{"id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","refund_amount":"s","sku_id":"z","spec_code":"z","spec_name":"z"}}},
  "erp_search_return_registers": {"a":{"o":{"created_at":"s","exchange_order_id":{"u":["i","z"]},"id":"i","logistics_company":"s","return_order_id":{"u":["i","z"]},"sender_phone":"s","target_id":{"u":["i","z"]},"target_no":{"u":["s","z"]},"target_type":{"u":["s","z"]},"tracking_no":"s","updated_at":"s"}}},
  "erp_search_after_sale_exchanges": {"a":{"o":{"compensate_direction":"s","compensate_order_no":"z","created_at":"s","exchange_no":"s","exchange_qty":"s","id":"i","is_cod":"b","is_return":"b","order_id":"i","order_no":"s","shop_id":"i","shop_name":"s","status":"s","status_label":"s","updated_at":"s"}}},
  "erp_get_after_sale_exchange": {"o":{"compensate_amount":"s","compensate_direction":"s","compensate_order_no":"z","created_at":"s","exchange_no":"s","exchange_qty":"s","id":"i","is_cod":"b","is_return":"b","order_id":"i","order_no":"s","shop_id":"i","shop_name":"s","status":"s","status_label":"s","updated_at":"s"}},
  "erp_list_after_sale_exchange_items": {"a":{"o":{"exchange_qty":"s","id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"z","spec_code":"z","spec_name":"z"}}},
  "erp_search_stock_flows": {"a":{"o":{"biz_type":"s","created_at":"s","direction":"s","flow_time":"s","id":"i","location_code":"s","location_id":"i","order_no":"s","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","source_no":"s","spec_code":"s","spec_name":"s","unit_id":"z","unit_name":"z","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_check_list_stock_checks": {"a":{"o":{"actual_total_qty":"s","audit_status":"s","check_no":"s","created_at":"s","current_total_qty":"s","gain_total_qty":"s","has_imported_result":"b","id":"i","item_count":"i","loss_total_qty":"s","result_entered_count":"i","status":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_check_get_stock_check": {"o":{"actual_total_qty":"s","audit_status":"s","check_no":"s","created_at":"s","current_total_qty":"s","gain_total_qty":"s","has_imported_result":"b","id":"i","item_count":"i","loss_total_qty":"s","result_entered_count":"i","status":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_stock_check_list_items": {"a":{"o":{"actual_qty":{"u":["s","z"]},"current_qty":"s","difference_qty":{"u":["s","z"]},"id":"i","location_code":"s","location_id":"i","product_code":"s","product_id":"i","product_name":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_stock_in_list_pending_putaways": {"a":{"o":{"pending_putaway_qty":"s","product_code":"s","product_id":"i","product_name":"s","putaway_qty":"s","received_qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_in_list_putaways": {"a":{"o":{"created_at":"s","id":"i","location_code":"s","location_id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","source_id":"i","source_no":"s","source_type":"s","spec_code":"s","spec_name":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_in_list_stock_ins": {"a":{"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","pending_putaway_qty":"s","putaway_qty":"s","status":"s","stock_in_no":"s","stock_in_type":"s","total_qty":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_in_get_stock_in": {"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","pending_putaway_qty":"s","putaway_qty":"s","status":"s","stock_in_no":"s","stock_in_type":"s","total_qty":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_stock_in_list_stock_in_items": {"a":{"o":{"amount":"s","cost_price":"s","id":"i","pending_putaway_qty":"s","product_code":"s","product_id":"i","product_name":"s","putaway_qty":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_stock_out_list_stock_outs": {"a":{"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","off_shelf_status":"s","status":"s","stock_out_no":"s","stock_out_type":"s","total_qty":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_out_get_stock_out": {"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","off_shelf_status":"s","status":"s","stock_out_no":"s","stock_out_type":"s","total_qty":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_stock_out_list_stock_out_items": {"a":{"o":{"id":"i","location_code":"s","location_id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_transfer_list_transfers": {"a":{"o":{"audit_status":"s","created_at":"s","from_warehouse_id":"i","from_warehouse_name":"s","id":"i","item_count":"i","status":"s","sync_stock":"b","to_warehouse_id":"i","to_warehouse_name":"s","total_qty":"s","transfer_no":"s","updated_at":"s"}}},
  "erp_transfer_get_transfer": {"o":{"audit_status":"s","created_at":"s","from_warehouse_id":"i","from_warehouse_name":"s","id":"i","item_count":"i","status":"s","sync_stock":"b","to_warehouse_id":"i","to_warehouse_name":"s","total_qty":"s","transfer_no":"s","updated_at":"s"}},
  "erp_transfer_list_transfer_items": {"a":{"o":{"id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_transfer_list_transfer_outs": {"a":{"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","status":"s","total_qty":"s","transfer_id":"i","transfer_out_no":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_transfer_get_transfer_out": {"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","status":"s","total_qty":"s","transfer_id":"i","transfer_out_no":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_transfer_list_transfer_out_items": {"a":{"o":{"id":"i","location_code":"s","location_id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_transfer_list_transfer_ins": {"a":{"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","status":"s","total_qty":"s","transfer_id":"i","transfer_in_no":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_transfer_get_transfer_in": {"o":{"audit_status":"s","created_at":"s","id":"i","item_count":"i","status":"s","total_qty":"s","transfer_id":"i","transfer_in_no":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_transfer_list_transfer_in_items": {"a":{"o":{"id":"i","location_code":"s","location_id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s"}}},
  "erp_stock_query_list_stock_summary": {"a":{"o":{"available_qty":"s","cost_price":"s","defective_qty":"s","is_below_warning":"b","normal_qty":"s","occupied_qty":"s","product_code":"s","product_id":"i","product_name":"s","shortage_qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","stock_cost":"s","total_qty":"s","warehouse_id":"i","warehouse_name":"s","warn_qty":"s"}}},
  "erp_stock_query_list_stock_locations": {"a":{"o":{"location_code":"s","location_id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","status":"s","stock_location_id":"i","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_query_list_stock_warnings": {"a":{"o":{"current_qty":"s","is_below_warning":"b","product_code":"s","product_id":"i","product_name":"s","sku_id":"i","spec_code":"s","spec_name":"s","warn_qty":"i"}}},
  "erp_stock_query_list_stock_costs": {"a":{"o":{"cost_price":"s","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","total_cost":"s"}}},
  "erp_stock_query_list_location_adjusts": {"a":{"o":{"adjust_no":"s","audit_status":"s","created_at":"s","id":"i","item_count":"i","status":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_query_get_location_adjust": {"o":{"adjust_no":"s","audit_status":"s","created_at":"s","id":"i","item_count":"i","status":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_stock_query_list_location_adjust_items": {"a":{"o":{"from_location_code":"s","from_location_id":"i","id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","to_location_code":"s","to_location_id":"i"}}},
  "erp_search_warehouses": {"a":{"o":{"address":{"u":["s","z"]},"code":"s","id":"i","is_default":"b","is_enabled":"b","name":"s","region":{"u":["s","z"]},"warehouse_type":"s"}}},
  "erp_get_warehouse": {"o":{"address":{"u":["s","z"]},"code":"s","id":"i","is_default":"b","is_enabled":"b","name":"s","region":{"u":["s","z"]},"warehouse_type":"s"}},
  "erp_logistics_list_logistics": {"a":{"o":{"company":{"u":["s","z"]},"id":"i","is_default":"b","is_enabled":"b","name":"s"}}},
  "erp_logistics_get_logistics": {"o":{"company":"s","id":"i","is_default":"b","is_enabled":"b","name":"s"}},
  "erp_logistics_list_freight_templates": {"a":{"o":{"base_fee":"s","charge_type":"s","consumable_fee":"s","continue_count":"i","continue_fee":"s","continue_weight":{"u":["s","z"]},"deduct_weight":{"u":["s","z"]},"ewaybill_fee":"s","first_count":"i","first_weight":{"u":["s","z"]},"id":"i","logistics_id":"i","logistics_name":"s","name":"s","operation_fee":"s","tax_fee":"s","tax_point":{"u":["s","z"]}}}},
  "erp_logistics_get_freight_template": {"o":{"base_fee":"s","charge_type":"s","consumable_fee":"s","continue_count":"i","continue_fee":"s","continue_weight":{"u":["s","z"]},"deduct_weight":{"u":["s","z"]},"ewaybill_fee":"s","first_count":"i","first_weight":{"u":["s","z"]},"id":"i","logistics_id":"i","logistics_name":"s","name":"s","operation_fee":"s","tax_fee":"s","tax_point":{"u":["s","z"]}}},
  "erp_logistics_calculate_freight": {"o":{"additional_fee":"s","additional_units":"i","base_fee":"s","charge_type":"s","charge_weight":{"u":["s","z"]},"consumable_fee":"s","ewaybill_fee":"s","fixed_tax_fee":"s","freight_fee":"s","operation_fee":"s","percentage_tax":"s","quantity":{"u":["s","i"]},"quantity_label":"s","tax_amount":"s","tax_point":"s","template_id":"i","template_name":"s","total_fee":"s"}},
  "erp_logistics_match_area_rule": {"o":{"area_rule":{"u":[{"o":{"city":{"u":["s","z"]},"freight_template_id":{"u":["i","z"]},"freight_template_name":{"u":["s","z"]},"id":"i","logistics_id":"i","logistics_name":"s","match_weight_max":{"u":["s","z"]},"match_weight_min":{"u":["s","z"]},"province":{"u":["s","z"]}}},"z"]},"city":{"u":["s","z"]},"freight_template":{"u":[{"o":{"base_fee":"s","charge_type":"s","consumable_fee":"s","continue_count":"i","continue_fee":"s","continue_weight":{"u":["s","z"]},"deduct_weight":{"u":["s","z"]},"ewaybill_fee":"s","first_count":"i","first_weight":{"u":["s","z"]},"id":"i","logistics_id":"i","logistics_name":"s","name":"s","operation_fee":"s","tax_fee":"s","tax_point":{"u":["s","z"]}}},"z"]},"logistics_id":"i","matched":"b","province":{"u":["s","z"]},"weight":{"u":["s","z"]}}},
  "erp_logistics_list_area_rules": {"a":{"o":{"city":{"u":["s","z"]},"freight_template_id":{"u":["i","z"]},"freight_template_name":{"u":["s","z"]},"id":"i","logistics_id":"i","logistics_name":"s","match_weight_max":{"u":["s","z"]},"match_weight_min":{"u":["s","z"]},"province":{"u":["s","z"]}}}},
  "erp_logistics_get_area_rule": {"o":{"city":{"u":["s","z"]},"freight_template_id":{"u":["i","z"]},"freight_template_name":{"u":["s","z"]},"id":"i","logistics_id":"i","logistics_name":"s","match_weight_max":{"u":["s","z"]},"match_weight_min":{"u":["s","z"]},"province":{"u":["s","z"]}}},
  "erp_logistics_list_print_templates": {"a":{"o":{"id":"i","is_default":"b","logistics_id":{"u":["i","z"]},"logistics_name":{"u":["s","z"]},"name":"s","paper_height":{"u":["s","z"]},"paper_width":{"u":["s","z"]},"template_type":"s"}}},
  "erp_logistics_get_print_template": {"o":{"id":"i","is_default":"b","logistics_id":{"u":["i","z"]},"logistics_name":{"u":["s","z"]},"name":"s","paper_height":{"u":["s","z"]},"paper_width":{"u":["s","z"]},"template_type":"s"}},
  "erp_print_get_print_batch": {"o":{"batch_no":"s","created_at":"z","order_count":"i","orders":{"a":{"o":{"id":"i","order_no":"s","receiver_address":"s","receiver_phone":"s"}}},"orders_truncated":"b","print_type":"s"}},
  "erp_shop_list_platforms": {"a":{"o":{"code":"s","id":"i","name":"s"}}},
  "erp_shop_get_platform": {"o":{"code":"s","id":"i","name":"s"}},
  "erp_shop_list_shops": {"a":{"o":{"api_configured":"b","auto_sync_order":"b","auto_sync_stock":"b","download_mode":{"u":["s","z"]},"enable_logistics_params":"b","id":"i","is_locked":"b","name":"s","platform_id":"i","platform_name":"s","supplier_id":{"u":["i","z"]},"supplier_name":{"u":["s","z"]}}}},
  "erp_shop_get_shop": {"o":{"api_configured":"b","auto_sync_order":"b","auto_sync_stock":"b","download_mode":"s","enable_logistics_params":"b","id":"i","is_locked":"b","name":"s","platform_id":"i","platform_name":"s","supplier_id":"i","supplier_name":"s"}},
  "erp_shop_get_api_status": {"o":{"api_configured":"b","shop_id":"i"}},
  "erp_list_warehouse_zones": {"a":{"o":{"code":"s","id":"i","name":{"u":["s","z"]},"sort_order":"i","warehouse_id":"i"}}},
  "erp_get_warehouse_zone": {"o":{"code":"s","id":"i","name":{"u":["s","z"]},"sort_order":"i","warehouse_id":"i"}},
  "erp_search_locations": {"a":{"o":{"code":"s","id":"i","is_enabled":"b","is_virtual":"b","warehouse_id":"i","zone_id":{"u":["i","z"]}}}},
  "erp_get_location": {"o":{"code":"s","id":"i","is_enabled":"b","is_virtual":"b","warehouse_id":"i","zone_id":{"u":["i","z"]}}},
  "erp_warehouse_get_location_capacity": {"o":{"capacity":"i","location_id":"i","remaining_qty":{"u":["s","z"]},"unlimited":"b","used_qty":"s"}},
  "erp_wave_list_wave_strategies": {"a":{"o":{"created_at":"s","id":"i","is_enabled":"b","load_no_type":"s","logistics_id":"i","logistics_name":"s","name":"s","sort_type":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s","wave_order_count":"i"}}},
  "erp_wave_get_wave_strategy": {"o":{"created_at":"s","id":"i","is_enabled":"b","load_no_type":"s","logistics_id":"i","logistics_name":"s","name":"s","sort_type":"s","updated_at":"s","warehouse_id":"i","warehouse_name":"s","wave_order_count":"i"}},
  "erp_wave_get_wave_strategy_pending_count": {"o":{"pending_count":"i","strategy_id":"i"}},
  "erp_wave_list_waves": {"a":{"o":{"created_at":"s","express_type":"s","id":"i","load_number_count":"i","name":"s","order_count":"i","strategy_id":"i","total_order_count":"i","updated_at":"s","wave_no":"s"}}},
  "erp_wave_get_wave": {"o":{"created_at":"s","express_type":"s","id":"i","load_number_count":"i","name":"s","order_count":"i","strategy_id":"i","total_order_count":"i","updated_at":"s","wave_no":"s"}},
  "erp_wave_list_wave_orders": {"a":{"o":{"audit_status":"s","id":"i","load_no":"s","logistics_id":"i","logistics_name":"s","order_no":"s","order_time":"s","sequence":"i","status":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_wave_list_load_numbers": {"a":{"o":{"express_type":"s","id":"i","load_no":"s","wave_id":"i"}}},
  "erp_search_purchase_prepays": {"a":{"o":{"audit_status":"s","created_at":"s","id":"i","order_amount":"z","order_id":"z","order_no":"z","prepay_amount":"s","prepay_no":"s","status":"s","supplier_id":"i","supplier_name":"s","unwritten_amount":"s","updated_at":"s","write_off_amount":"s"}}},
  "erp_get_purchase_prepay": {"o":{"audit_status":"s","created_at":"s","id":"i","order_amount":"z","order_id":"z","order_no":"z","prepay_amount":"s","prepay_no":"s","status":"s","supplier_id":"i","supplier_name":"s","unwritten_amount":"s","updated_at":"s","write_off_amount":"s"}},
  "erp_get_purchase_prepay_voucher": {"u":[{"o":{"business_date":"z","created_at":"s","id":"i","item_count":"i","record_date":"z","status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},"z"]},
  "erp_search_purchase_invoices": {"a":{"o":{"amount":"s","audit_status":"s","business_type":"z","cost_accounted_at":"z","created_at":"s","id":"i","invoice_no":"s","invoice_type":"s","invoice_type_name":"s","net_amount":"s","status":"s","supplier_id":"i","supplier_name":"z","tax_amount":"s","tax_rate":"z","updated_at":"s","write_off_amount":"s","write_off_status":"s"}}},
  "erp_get_purchase_invoice": {"o":{"amount":"s","audit_status":"s","business_type":"z","cost_accounted_at":"z","created_at":"s","id":"i","invoice_no":"s","invoice_type":"s","invoice_type_name":"s","net_amount":"s","status":"s","supplier_id":"i","supplier_name":"z","tax_amount":"s","tax_rate":"z","updated_at":"s","write_off_amount":"s","write_off_status":"s"}},
  "erp_get_purchase_invoice_cost_detail": {"a":{"o":{"allocated_amount":"s","new_amount":"s","new_cost_price":"s","original_amount":"s","original_cost_price":"s","product_id":"i","qty":"s","sku_id":{"u":["i","z"]},"stock_in_id":"i","stock_in_item_id":"i","stock_in_no":"s"}}},
  "erp_report_purchase_orders": {"o":{"items":{"a":{"o":{"arrived_qty":"s","in_transit_qty":"s","order_count":"i","supplier_id":"i","supplier_name":"s","total_amount":"s","total_qty":"s"}}},"summary":{"o":{"arrived_qty":"s","in_transit_qty":"s","order_count":"i","supplier_count":"i","total_amount":"s","total_qty":"s"}}}},
  "erp_report_purchase_inbounds": {"o":{"items":{"a":{"o":{"amount":"s","product_code":"s","product_id":"i","product_name":"s","purchaser_id":"i","purchaser_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","stock_in_count":"i","supplier_id":"i","supplier_name":"s"}}},"summary":{"o":{"amount":"s","qty":"s","stock_in_count":"i"}}}},
  "erp_report_purchase_invoices": {"o":{"items":{"a":{"o":{"amount":"s","invoice_count":"i","net_amount":"s","supplier_id":"i","supplier_name":"s","tax_amount":"s","write_off_amount":"s","write_off_status":"s"}}},"summary":{"o":{"amount":"s","invoice_count":"i","net_amount":"s","tax_amount":"s","write_off_amount":"s"}}}},
  "erp_report_inventory_in_transit": {"o":{"items":{"a":{"o":{"current_qty":"s","in_transit_qty":"s","product_code":"s","product_id":"i","product_name":"s","sku_id":"i","spec_code":"s","spec_name":"s","total_qty":"s"}}},"summary":{"o":{"current_qty":"s","in_transit_qty":"s","sku_count":"i","total_qty":"s"}}}},
  "erp_invoice_list_invoices": {"a":{"o":{"amount":"s","company":"s","created_at":"s","document_type":"s","id":"i","invoice_no":"s","invoice_title":"s","invoice_type":"s","invoice_type_name":"s","is_received":"b","received_amount":"s","saved_at":"s","status":"s","unreceived_amount":"s","updated_at":"s"}}},
  "erp_invoice_get_invoice": {"o":{"amount":"s","company":"s","created_at":"s","document_type":"s","id":"i","invoice_no":"s","invoice_title":"s","invoice_type":"s","invoice_type_name":"s","is_received":"b","received_amount":"s","saved_at":"s","status":"s","unreceived_amount":"s","updated_at":"s"}},
  "erp_invoice_list_supplier_account_balances": {"a":{"o":{"account_no":"s","balance":"s","balance_synced_at":"z","bank":"s","bank_no":{"u":["s","z"]},"branch":{"u":["s","z"]},"created_at":"s","id":"i","is_default":"b","kingdee_sync_status":"s","supplier_code":"s","supplier_id":"i","supplier_name":"s","updated_at":"s"}}},
  "erp_invoice_get_supplier_account_balance": {"o":{"account_no":"s","balance":"s","balance_synced_at":"z","bank":"s","bank_no":"s","branch":"s","created_at":"s","id":"i","is_default":"b","kingdee_sync_status":"s","supplier_code":"s","supplier_id":"i","supplier_name":"s","updated_at":"s"}},
  "erp_account_subject_get_subject_meta": {"o":{"balance_directions":{"a":{"o":{"code":"s","name":"s"}}},"seed_note":"s","seed_version":"s","subject_categories":{"a":{"o":{"code":"s","name":"s"}}}}},
  "erp_account_subject_list_subjects": {"a":{"o":{"balance_direction":"s","balance_direction_name":"s","category":"s","category_name":"s","code":"s","created_at":"s","id":"i","is_auxiliary":"b","is_bank":"b","is_cash":"b","is_cash_equivalent":"b","is_enabled":"b","is_journal":"b","is_qty_amount":"b","measure_unit":"z","name":"s","project_category_code":"z","project_category_id":"z","project_category_name":"z","updated_at":"s"}}},
  "erp_account_subject_get_subject": {"o":{"balance_direction":"s","balance_direction_name":"s","category":"s","category_name":"s","code":"s","created_at":"s","id":"i","is_auxiliary":"b","is_bank":"b","is_cash":"b","is_cash_equivalent":"b","is_enabled":"b","is_journal":"b","is_qty_amount":"b","measure_unit":"z","name":"s","project_category_code":"s","project_category_id":"i","project_category_name":"s","updated_at":"s"}},
  "erp_account_subject_list_project_categories": {"a":{"o":{"code":"s","created_at":"s","id":"i","name":"s","updated_at":"s"}}},
  "erp_account_subject_get_project_category": {"o":{"code":"s","created_at":"s","id":"i","name":"s","updated_at":"s"}},
  "erp_voucher_list_vouchers": {"a":{"o":{"business_date":{"u":["s","z"]},"created_at":"s","id":"i","item_count":"i","record_date":{"u":["s","z"]},"status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}}},
  "erp_voucher_get_voucher": {"o":{"business_date":{"u":["s","z"]},"created_at":"s","id":"i","item_count":"i","record_date":{"u":["s","z"]},"status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},
  "erp_voucher_list_voucher_items": {"a":{"o":{"credit":"s","debit":"s","id":"i","subject_code":"s","subject_id":"i","subject_name":"s"}}},
  "erp_search_expenses": {"a":{"o":{"amount":"s","audit_status":"s","created_at":"s","customer_id":{"u":["i","z"]},"customer_name":{"u":["s","z"]},"expense_no":"s","expense_type":{"u":["s","z"]},"id":"i","receipt_id":"z","receipt_no":"z","status":"s","updated_at":"s"}}},
  "erp_finance_doc_get_expense": {"o":{"amount":"s","audit_status":"s","created_at":"s","customer_id":"z","customer_name":"z","expense_no":"s","expense_type":"z","id":"i","receipt_id":"z","receipt_no":"z","status":"s","updated_at":"s"}},
  "erp_finance_doc_get_expense_voucher": {"u":[{"o":{"business_date":"z","created_at":"s","id":"i","item_count":"i","record_date":"z","status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},"z"]},
  "erp_search_incomes": {"a":{"o":{"amount":"s","audit_status":"s","created_at":"s","id":"i","income_no":"s","income_type":{"u":["s","z"]},"remaining_amount":"s","status":"s","supplier_id":{"u":["i","z"]},"supplier_name":{"u":["s","z"]},"updated_at":"s","write_off_amount":"s","write_off_status":"s"}}},
  "erp_finance_doc_get_income": {"o":{"amount":"s","audit_status":"s","created_at":"s","id":"i","income_no":"s","income_type":"z","remaining_amount":"s","status":"s","supplier_id":"z","supplier_name":"z","updated_at":"s","write_off_amount":"s","write_off_status":"s"}},
  "erp_finance_doc_get_income_voucher": {"u":[{"o":{"business_date":"z","created_at":"s","id":"i","item_count":"i","record_date":"z","status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},"z"]},
  "erp_search_payments": {"a":{"o":{"amount":"s","audit_status":"s","created_at":"s","has_warning":"b","id":"i","income_id":"z","income_no":"z","invoice_id":"z","invoice_no":"z","pay_method":{"u":["s","z"]},"payment_no":"s","payment_type":{"u":["s","z"]},"prepay_id":"z","prepay_no":"z","status":"s","stock_in_id":"z","stock_in_no":"z","supplier_id":"i","supplier_name":"s","updated_at":"s"}}},
  "erp_finance_doc_get_payment": {"o":{"amount":"s","audit_status":"s","created_at":"s","has_warning":"b","id":"i","income_id":"z","income_no":"z","invoice_id":"z","invoice_no":"z","pay_method":{"u":["s","z"]},"payment_no":"s","payment_type":{"u":["s","z"]},"prepay_id":"z","prepay_no":"z","status":"s","stock_in_id":"z","stock_in_no":"z","supplier_id":"i","supplier_name":"s","updated_at":"s"}},
  "erp_finance_doc_get_payment_voucher": {"u":[{"o":{"business_date":"z","created_at":"s","id":"i","item_count":"i","record_date":"z","status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},"z"]},
  "erp_search_receipts": {"a":{"o":{"amount":"s","audit_status":"s","created_at":"s","customer_id":"i","customer_name":"s","expense_id":"z","expense_no":"z","has_warning":"b","id":"i","invoice_id":"z","invoice_no":"z","order_id":"z","order_no":"z","receipt_no":"s","sale_type":"z","settle_type":"z","status":"s","updated_at":"s"}}},
  "erp_finance_doc_get_receipt": {"o":{"amount":"s","audit_status":"s","created_at":"s","customer_id":"i","customer_name":"s","expense_id":"z","expense_no":"z","has_warning":"b","id":"i","invoice_id":"z","invoice_no":"z","order_id":"z","order_no":"z","receipt_no":"s","sale_type":"z","settle_type":"z","status":"s","updated_at":"s"}},
  "erp_finance_doc_get_receipt_voucher": {"u":[{"o":{"business_date":"z","created_at":"s","id":"i","item_count":"i","record_date":"z","status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},"z"]},
  "erp_search_settlements": {"a":{"o":{"amount":"s","created_at":"s","id":"i","platform_name":"z","platform_no":{"u":["s","z"]},"receivable_id":"z","receivable_no":"z","settlement_date":{"u":["s","z"]},"settlement_no":"s","shop_id":"z","shop_name":"z","status":"s","updated_at":"s"}}},
  "erp_finance_doc_get_settlement": {"o":{"amount":"s","created_at":"s","id":"i","platform_name":"z","platform_no":{"u":["s","z"]},"receivable_id":"z","receivable_no":"z","settlement_date":{"u":["s","z"]},"settlement_no":"s","shop_id":"z","shop_name":"z","status":"s","updated_at":"s"}},
  "erp_finance_doc_get_settlement_voucher": {"u":[{"o":{"business_date":"z","created_at":"s","id":"i","item_count":"i","record_date":"z","status":"s","total_credit":"s","total_debit":"s","updated_at":"s","voucher_no":"s","voucher_word":"s"}},"z"]},
  "erp_list_my_notifications": {"o":{"items":{"a":{"o":{"body":"s","business_state":"s","category":"s","created_at":"s","cursor":"s","event_code":"s","id":"i","read_at":"z","read_state":"s","target":{"o":{"id":"i","type":"s"}},"title":"s"}}}}},
  "erp_notification_count_unread": {"o":{"count":"i"}},
  "erp_get_my_notification": {"o":{"body":"s","business_state":"s","category":"s","created_at":"s","cursor":"s","event_code":"s","id":"i","read_at":"z","read_state":"s","target":{"o":{"id":"i","type":"s"}},"title":"s"}},
  "erp_get_stock_settings": {"o":{"floating_cost_enabled":"b","id":"z","off_shelf_below_warning":"b","shop_stock_enabled":"b","sync_stock_default":"b","updated_at":"z"}},
  "erp_stock_settlement_list_shop_stocks": {"a":{"o":{"available_qty":"s","effective_end":{"u":["s","z"]},"effective_start":{"u":["s","z"]},"exclusive_qty":"s","id":"i","is_effective":"b","is_enabled":"b","is_sync":"b","occupied_qty":"s","platform_id":"i","platform_name":"s","product_code":"s","product_id":"i","product_name":"s","shop_available_qty":"s","shop_id":"i","shop_name":"s","sku_id":"i","spec_code":"s","spec_name":"s","stock_qty":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_stock_settlement_get_shop_stock": {"o":{"available_qty":"s","effective_end":"z","effective_start":"z","exclusive_qty":"s","id":"i","is_effective":"b","is_enabled":"b","is_sync":"b","occupied_qty":"s","platform_id":"i","platform_name":"s","product_code":"s","product_id":"i","product_name":"s","shop_available_qty":"s","shop_id":"i","shop_name":"s","sku_id":"i","spec_code":"s","spec_name":"s","stock_qty":"s","warehouse_id":"i","warehouse_name":"s"}},
  "erp_stock_settlement_list_periods": {"a":{"o":{"item_count":"i","settlement_date":"s","total_cost":"s","total_qty":"s"}}},
  "erp_stock_settlement_list_settlements": {"a":{"o":{"cost_price":"s","id":"i","product_code":"s","product_id":"i","product_name":"s","qty":"s","settlement_date":"s","sku_id":"i","spec_code":"s","spec_name":"s","total_cost":"s"}}},
  "erp_stock_settlement_list_cost_rewrite_logs": {"a":{"o":{"batch_no":"s","cost_source":"s","created_at":"s","id":"i","new_cost_price":"s","new_total_cost":"s","old_cost_price":"s","old_total_cost":"s","period_end":"s","period_start":"s","product_code":"s","product_id":"i","product_name":"s","qty":"s","sku_id":"i","spec_code":"s","spec_name":"s","target_id":"i","target_type":"s","warehouse_id":"i","warehouse_name":"s"}}},
  "erp_list_approval_flows": {"a":{"o":{"doc_type":"s","id":"i","name":"s"}}},
  "erp_get_approval_records": {"o":{"doc_id":"i","doc_type":"s","records":{"a":{"o":{"action":"s","audit_time":{"u":["s","z"]},"created_at":"s","id":"i","status":"s","step":"i"}}}}},
  "erp_list_approval_doc_types": {"a":{"o":{"code":"s","name":"s"}}},
  "erp_list_approval_flow_configs": {"a":{"o":{"created_at":"s","doc_type":"s","doc_type_label":"s","id":"i","name":"s","steps":{"a":{"o":{"approver_count":"i","name":"s","step":"i"}}},"updated_at":"s"}}},
  "erp_dashboard_get_dashboard_overview": {"o":{"as_of":"s","metrics":{"a":{"o":{"change_rate":"z","key":"s","label":"s","previous_value":{"u":["s","i","z"]},"unit":"s","value":{"u":["s","i"]}}}},"sales_trend":{"o":{"end_date":"s","items":{"a":{"o":{"amount":"s","end_date":"s","order_count":"i","period":"s","start_date":"s"}}},"period":"s","start_date":"s"}}}},
  "erp_company_list_ip_rules": {"a":{"o":{"id":"i","ip_address":"s","rule_type":"s"}}},
  "erp_company_get_ip_rule": {"o":{"id":"i","ip_address":"s","rule_type":"s"}},
  "erp_assistant_get_admin_config": {"o":{"api_key_configured":"b","concurrent_requests_per_user":"i","daily_requests_per_user":{"u":["i","z"]},"enabled":"b","monthly_budget":{"u":["s","z"]},"requests_per_minute":"i"}},
  "erp_assistant_get_admin_usage": {"o":{"items":{"a":{"o":{"completed_at":"s","completion_tokens":"i","cost_status":"s","estimated_cost":"s","latency_ms":"i","prompt_tokens":"i","request_id":"s","staff_id":"i","staff_name":"s","started_at":"s","status":"s","total_tokens":"i"}}},"summary":{"o":{"known_completion_tokens":"i","known_cost":"s","known_prompt_tokens":"i","monthly_budget":{"u":["s","z"]},"monthly_budget_committed":"s","request_count":"i","unknown_cost_count":"i","unknown_token_count":"i"}}}},
  "erp_assistant_get_my_usage": {"o":{"items":{"a":{"o":{"completed_at":"z","completion_tokens":"z","cost_status":"s","estimated_cost":"z","latency_ms":"i","prompt_tokens":"i","request_id":"s","started_at":"s","status":"s","total_tokens":"i"}}},"limits":{"o":{"concurrent_requests_per_user":"i","daily_requests_per_user":"z","requests_per_minute":"i"}},"summary":{"o":{"known_completion_tokens":"i","known_cost":"s","known_prompt_tokens":"i","request_count":"i","unknown_cost_count":"i","unknown_token_count":"i"}}}},
  "erp_list_my_approvals": {"a":{"o":{"approval_status":"s","audit_status":"s","business_status":"s","current_step":"z","doc_id":"i","doc_no":"z","doc_type":"s","doc_type_label":"s","flow_id":"z","flow_name":"z","latest_action":"s","latest_action_at":"z","latest_action_status":"s","submitted_at":"z","total_steps":"i"}}},
  "erp_get_my_approval_detail": {"o":{"approval":{"o":{"approved_steps":{"a":"i"},"approved_steps_truncated":"b","current_step":"z","flow_name":"z","status":"s","total_steps":"i"}},"can_approve":"b","can_reject":"b","can_unaudit":"b","detail":{"o":{"audit_status":"s","created_at":"s","id":"i","plan_date":"z","plan_no":"s","status":"s","supplier_id":"i","supplier_name":"s","total_amount":"s","total_qty":"s","updated_at":"s"}},"doc_id":"i","doc_no":"z","doc_type":"s","doc_type_label":"s","flow":{"o":{"doc_type":"s","doc_type_label":"s","id":"i","name":"s"}},"records":{"a":{"o":{"action":"s","audit_time":"z","created_at":"s","id":"i","status":"s","step":"i","step_name":{"u":["s","z"]}}}},"records_truncated":"b"}}
}
"""

_COMPACT_SCHEMAS: dict[str, Any] = json.loads(_COMPACT_SCHEMAS_JSON)

# 样例中只出现 null 的字段，依据领域模型/序列化规则声明其非空类型。
_NULLABLE_TYPE_OVERRIDES = {
    "actual_weight_g": "s",
    "art_no": "s",
    "balance_synced_at": "s",
    "brand_id": "i",
    "brand_name": "s",
    "business_type": "s",
    "change_rate": "s",
    "compensate_order_no": "s",
    "cost_accounted_at": "s",
    "expected_weight_g": "s",
    "expense_id": "i",
    "goods_status": "s",
    "income_id": "i",
    "invoice_id": "i",
    "latest_action_at": "s",
    "listed_at": "s",
    "measure_unit": "s",
    "parent_id": "i",
    "current_step": "i",
    "doc_no": "s",
    "flow_id": "i",
    "flow_name": "s",
    "pay_method": "s",
    "pay_time": "s",
    "plan_id": "i",
    "plan_date": "s",
    "prepay_id": "i",
    "read_at": "s",
    "receivable_id": "i",
    "receivable_no": "s",
    "receipt_id": "i",
    "sale_type": "s",
    "settle_type": "s",
    "ship_time": "s",
    "shipped_at": "s",
    "sku_name": "s",
    "stock_in_status": "s",
    "submitted_at": "s",
    "tax_rate": "s",
}
_JSON_TYPES = {"s": "string", "i": "integer", "b": "boolean"}

# 默认要求返回schema声明的全部固定字段。仅将受当前权限裁剪、动态能力项
# 或历史JSON可缺失字段列为optional，避免客户端把安全裁剪误认为字段漏项。
_OPTIONAL_OUTPUT_PATHS_BY_TOOL = {
    "erp_assistant_capabilities": {
        "features[].document_types", "features[].reason",
    },
    "erp_get_my_approval_detail": {"flow"},
    "erp_logistics_list_freight_templates": {
        "[].base_fee", "[].continue_fee", "[].operation_fee",
        "[].consumable_fee", "[].ewaybill_fee", "[].tax_fee", "[].tax_point",
    },
    "erp_logistics_get_freight_template": {
        "base_fee", "continue_fee", "operation_fee", "consumable_fee",
        "ewaybill_fee", "tax_fee", "tax_point",
    },
    "erp_logistics_calculate_freight": {
        "additional_fee", "base_fee", "consumable_fee", "ewaybill_fee",
        "fixed_tax_fee", "freight_fee", "operation_fee", "percentage_tax",
        "tax_amount", "tax_point", "total_fee",
    },
    "erp_logistics_match_area_rule": {
        "freight_template.base_fee", "freight_template.continue_fee",
        "freight_template.operation_fee", "freight_template.consumable_fee",
        "freight_template.ewaybill_fee", "freight_template.tax_fee",
        "freight_template.tax_point",
    },
    "erp_report_purchase_orders": {
        "items[].total_amount", "summary.total_amount",
    },
    "erp_report_purchase_inbounds": {
        "items[].amount", "summary.amount",
    },
    "erp_report_purchase_invoices": {
        "items[].amount", "items[].net_amount", "items[].tax_amount",
        "items[].write_off_amount", "summary.amount", "summary.net_amount",
        "summary.tax_amount", "summary.write_off_amount",
    },
    "erp_stock_in_list_stock_in_items": {"[].amount", "[].cost_price"},
    "erp_stock_query_list_stock_summary": {"[].cost_price", "[].stock_cost"},
    "erp_stock_query_list_stock_costs": {"[].cost_price", "[].total_cost"},
    "erp_stock_settlement_list_periods": {"[].total_cost"},
    "erp_stock_settlement_list_settlements": {
        "[].cost_price", "[].total_cost",
    },
    "erp_stock_settlement_list_cost_rewrite_logs": {
        "[].old_cost_price", "[].new_cost_price",
        "[].old_total_cost", "[].new_total_cost",
    },
    "erp_search_purchase_returns": {"[].refund_amount"},
    "erp_get_purchase_return": {"refund_amount"},
    "erp_list_purchase_return_items": {"[].unit_price", "[].amount"},
    "erp_get_purchase_return_refund": {"amount"},
    "erp_search_after_sale_returns": {
        "[].refund_amount", "[].freight", "[].freight_refunded",
    },
    "erp_get_after_sale_return": {
        "refund_amount", "freight", "freight_refunded",
    },
    "erp_list_after_sale_return_items": {"[].refund_amount"},
    "erp_get_after_sale_exchange": {"compensate_amount"},
    "erp_get_purchase_invoice_cost_detail": {
        "[].allocated_amount", "[].new_amount", "[].new_cost_price",
        "[].original_amount", "[].original_cost_price", "[].product_id",
        "[].qty", "[].sku_id", "[].stock_in_id", "[].stock_in_item_id",
    },
    "erp_list_supplier_accounts": {"[].balance"},
    "erp_list_customer_accounts": {"[].balance"},
}


def _collect_observed_types(node: Any, field_name: str | None, result: dict[str, set[str]]) -> None:
    if isinstance(node, str):
        if node != "z" and field_name:
            result.setdefault(field_name, set()).add(node)
        return
    if not isinstance(node, dict):
        raise TypeError(f"Invalid compact schema node: {node!r}")
    if "o" in node:
        for name, child in node["o"].items():
            _collect_observed_types(child, name, result)
    elif "a" in node:
        _collect_observed_types(node["a"], field_name, result)
    elif "u" in node:
        for child in node["u"]:
            _collect_observed_types(child, field_name, result)
    else:
        raise TypeError(f"Invalid compact schema node: {node!r}")


_OBSERVED_TYPES: dict[str, set[str]] = {}
for _schema in _COMPACT_SCHEMAS.values():
    _collect_observed_types(_schema, None, _OBSERVED_TYPES)


_APPROVAL_DETAIL_SOURCE_TOOLS = {
    "purchase_plan": "erp_get_purchase_plan",
    "purchase_order": "erp_get_purchase_order",
    "purchase_prepay": "erp_get_purchase_prepay",
    "purchase_return": "erp_get_purchase_return",
    "purchase_invoice": "erp_get_purchase_invoice",
    "stock_in": "erp_stock_in_get_stock_in",
    "stock_out": "erp_stock_out_get_stock_out",
    "transfer": "erp_transfer_get_transfer",
    "transfer_out": "erp_transfer_get_transfer_out",
    "transfer_in": "erp_transfer_get_transfer_in",
    "stock_check": "erp_stock_check_get_stock_check",
    "location_adjust": "erp_stock_query_get_location_adjust",
    "order": "erp_order_get_order",
}
_APPROVAL_DETAIL_OPTIONAL_FIELDS_BY_TYPE = {
    "stock_in": frozenset({"supplier_name"}),
    "stock_check": frozenset({"total_qty"}),
}


def _build_approval_detail_variants() -> dict[str, Any]:
    variants = []
    for doc_type, source_tool in _APPROVAL_DETAIL_SOURCE_TOOLS.items():
        source_fields = _COMPACT_SCHEMAS[source_tool].get("o", {})
        projected_fields = (
            _APPROVAL_DETAIL_FIELDS[doc_type]
            | _APPROVAL_DETAIL_MONEY_FIELDS.get(doc_type, frozenset())
        )
        properties = {}
        for field in sorted(projected_fields):
            compact_type = source_fields.get(field)
            if compact_type is None:
                if not _OBSERVED_TYPES.get(field) and field not in _NULLABLE_TYPE_OVERRIDES:
                    raise ValueError(
                        f"Cannot infer approval detail output type: {doc_type}.{field}"
                    )
                compact_type = "z"
            properties[field] = compact_type
        optional_fields = (
            _APPROVAL_DETAIL_MONEY_FIELDS.get(doc_type, frozenset())
            | _APPROVAL_DETAIL_OPTIONAL_FIELDS_BY_TYPE.get(doc_type, frozenset())
        )
        variants.append({"o": properties, "optional": sorted(optional_fields)})
    return {"u": variants}


_COMPACT_SCHEMAS["erp_get_my_approval_detail"]["o"]["detail"] = (
    _build_approval_detail_variants()
)


def _expand_schema(
    node: Any,
    field_name: str | None = None,
    path: str = "",
    optional_paths: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    if isinstance(node, str):
        if node == "z":
            candidates = _OBSERVED_TYPES.get(field_name or "", set())
            if not candidates and field_name:
                override = _NULLABLE_TYPE_OVERRIDES.get(field_name)
                candidates = {override} if override else set()
            if not candidates:
                return {"type": "null"}
            expanded = [_expand_schema(candidate) for candidate in sorted(candidates)]
            nullable_type = expanded[0] if len(expanded) == 1 else {"anyOf": expanded}
            return {"anyOf": [nullable_type, {"type": "null"}]}
        try:
            return {"type": _JSON_TYPES[node]}
        except KeyError as exc:
            raise ValueError(f"Unsupported compact schema code: {node}") from exc

    if not isinstance(node, dict):
        raise TypeError(f"Invalid compact schema node: {node!r}")
    if "o" in node:
        child_paths = {
            name: f"{path}.{name}" if path else name
            for name in node["o"]
        }
        return {
            "type": "object",
            "properties": {
                name: _expand_schema(
                    child, name, child_paths[name], optional_paths,
                )
                for name, child in node["o"].items()
            },
            "required": sorted(
                name for name in node["o"]
                if child_paths[name] not in optional_paths
                and name not in node.get("optional", ())
            ),
            "additionalProperties": False,
        }
    if "a" in node:
        item_path = f"{path}[]"
        return {
            "type": "array",
            "items": _expand_schema(node["a"], path=item_path, optional_paths=optional_paths),
        }
    if "u" in node:
        branches = [
            {"type": "null"} if child == "z" else _expand_schema(
                child, field_name, path, optional_paths,
            )
            for child in node["u"]
        ]
        return {"anyOf": branches}
    raise TypeError(f"Invalid compact schema node: {node!r}")


_TOOL_DATA_SCHEMAS = {
    name: _expand_schema(
        schema,
        optional_paths=frozenset(_OPTIONAL_OUTPUT_PATHS_BY_TOOL.get(name, ())),
    )
    for name, schema in _COMPACT_SCHEMAS.items()
}


def get_tool_data_schema(name: str) -> dict[str, Any]:
    """返回工具专属、禁止未声明字段的 data schema 副本。"""
    try:
        return deepcopy(_TOOL_DATA_SCHEMAS[name])
    except KeyError as exc:
        raise KeyError(f"Missing MCP output schema for {name}") from exc


def get_tool_output_schema(name: str, envelope_schema: dict[str, Any]) -> dict[str, Any]:
    """返回同时覆盖成功信封和 MCP 安全错误的工具输出 schema。"""
    schema = deepcopy(envelope_schema)
    schema["properties"]["data"] = get_tool_data_schema(name)
    return include_error_output_schema(schema)


def include_error_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """MCP 工具可能返回结构化错误；输出契约必须同时允许成功与错误形状。"""
    success_schema = deepcopy(schema)
    definitions = success_schema.pop("$defs", None)
    output_schema: dict[str, Any] = {
        "type": "object",
        "anyOf": [
            success_schema,
            {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "request_id": {"type": "string"},
                },
                "required": ["code", "message", "request_id"],
                "additionalProperties": False,
            },
        ],
    }
    if definitions:
        output_schema["$defs"] = definitions
    return output_schema

