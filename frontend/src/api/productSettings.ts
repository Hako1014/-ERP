import { api } from '@/api/client'

export interface ProductCategory {
  id: number
  name: string
  parent_id?: number | null
  sort_order: number
}

export interface ProductBrand {
  id: number
  name: string
  image?: string | null
}

export interface ProductUnit {
  id: number
  name: string
  code?: string | null
}

export interface ProductUnitGroupItem {
  unit_id: number
  conversion_rate: string | number
}

export interface ProductUnitGroup {
  id: number
  name: string
  main_unit_id: number
  items: ProductUnitGroupItem[]
}

export interface ProductSpecType {
  id: number
  name: string
  category_id?: number | null
}

export interface ProductSpecValue {
  id: number
  spec_type_id: number
  name: string
  code?: string | null
  sort_order: number
}

export const productSettingsApi = {
  listCategories(): Promise<ProductCategory[]> {
    return api.get<ProductCategory[]>('/product-settings/categories')
  },
  createCategory(payload: Partial<ProductCategory> & { name: string }): Promise<ProductCategory> {
    return api.post<ProductCategory>('/product-settings/categories', payload)
  },
  updateCategory(id: number, payload: Partial<ProductCategory>): Promise<ProductCategory> {
    return api.put<ProductCategory>(`/product-settings/categories/${id}`, payload)
  },
  deleteCategory(id: number): Promise<void> {
    return api.delete<void>(`/product-settings/categories/${id}`)
  },
  listBrands(): Promise<ProductBrand[]> {
    return api.get<ProductBrand[]>('/product-settings/brands')
  },
  createBrand(payload: { name: string; image?: string | null }): Promise<ProductBrand> {
    return api.post<ProductBrand>('/product-settings/brands', payload)
  },
  updateBrand(id: number, payload: Partial<ProductBrand>): Promise<ProductBrand> {
    return api.put<ProductBrand>(`/product-settings/brands/${id}`, payload)
  },
  deleteBrand(id: number): Promise<void> {
    return api.delete<void>(`/product-settings/brands/${id}`)
  },
  listUnits(): Promise<ProductUnit[]> {
    return api.get<ProductUnit[]>('/product-settings/units')
  },
  createUnit(payload: { name: string; code?: string | null }): Promise<ProductUnit> {
    return api.post<ProductUnit>('/product-settings/units', payload)
  },
  updateUnit(id: number, payload: Partial<ProductUnit>): Promise<ProductUnit> {
    return api.put<ProductUnit>(`/product-settings/units/${id}`, payload)
  },
  deleteUnit(id: number): Promise<void> {
    return api.delete<void>(`/product-settings/units/${id}`)
  },
  listUnitGroups(): Promise<ProductUnitGroup[]> {
    return api.get<ProductUnitGroup[]>('/product-settings/unit-groups')
  },
  createUnitGroup(
    payload: Omit<ProductUnitGroup, 'id'>,
  ): Promise<ProductUnitGroup> {
    return api.post<ProductUnitGroup>('/product-settings/unit-groups', payload)
  },
  updateUnitGroup(
    id: number,
    payload: Partial<Omit<ProductUnitGroup, 'id'>>,
  ): Promise<ProductUnitGroup> {
    return api.put<ProductUnitGroup>(
      `/product-settings/unit-groups/${id}`,
      payload,
    )
  },
  deleteUnitGroup(id: number): Promise<void> {
    return api.delete<void>(`/product-settings/unit-groups/${id}`)
  },
  listSpecTypes(): Promise<ProductSpecType[]> {
    return api.get<ProductSpecType[]>('/product-settings/spec-types')
  },
  createSpecType(
    payload: { name: string; category_id?: number | null },
  ): Promise<ProductSpecType> {
    return api.post<ProductSpecType>('/product-settings/spec-types', payload)
  },
  updateSpecType(
    id: number,
    payload: Partial<ProductSpecType>,
  ): Promise<ProductSpecType> {
    return api.put<ProductSpecType>(`/product-settings/spec-types/${id}`, payload)
  },
  deleteSpecType(id: number): Promise<void> {
    return api.delete<void>(`/product-settings/spec-types/${id}`)
  },
  listSpecValues(specTypeId: number): Promise<ProductSpecValue[]> {
    return api.get<ProductSpecValue[]>(
      `/product-settings/spec-types/${specTypeId}/values`,
    )
  },
  createSpecValue(
    specTypeId: number,
    payload: { name: string; code?: string | null; sort_order?: number },
  ): Promise<ProductSpecValue> {
    return api.post<ProductSpecValue>(
      `/product-settings/spec-types/${specTypeId}/values`,
      payload,
    )
  },
  updateSpecValue(
    id: number,
    payload: Partial<ProductSpecValue>,
  ): Promise<ProductSpecValue> {
    return api.put<ProductSpecValue>(
      `/product-settings/spec-values/${id}`,
      payload,
    )
  },
  deleteSpecValue(id: number): Promise<void> {
    return api.delete<void>(`/product-settings/spec-values/${id}`)
  },
}
