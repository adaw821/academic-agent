# 我的 Java / Spring 速查笔记

> 忘了就翻这里。每问一个新语法就往下加一条。
> 规则:通用语法记这里,**别塞进代码注释**。

---

## 一、基础语法符号

### `<类型>` 泛型(Generics)
告诉容器/类"它处理哪种数据类型",换来编译期类型检查 + 取出来免强转。
```java
List<String> list;                          // 装 String 的列表
Map<Integer, List<课程>> m;                  // key=Integer, value=List<课程>
BaseMapper<ScheduleItemEntity>              // 操作 ScheduleItemEntity 的 Mapper
ServiceImpl<ScheduleItemMapper, ScheduleItemEntity>  // 两个泛型:用哪个Mapper、操作哪个实体
```
看到尖括号 = "在指定类型"。

### `this.`
指"当前这个对象自己"。`this.字段` = 当前对象的那个字段。
名字撞车时用它区分**字段**和**参数**:
```java
this.scheduleService = scheduleService;
//   └字段(留得住)      └参数(马上走)
```
参数名取得不一样时(如 `svc`)就不用 `this.`。

### `final`
"赋值一次后不准再改"。**只管能不能改,不管谁能用**(谁能用是 private/public 管的)。
```java
private final ScheduleService scheduleService;  // private=范围, final=锁定不可改
```

---

## 二、类 / 接口

### 字段 vs 局部变量
- **字段**:写在「类那层大括号」里、方法外 → 全类方法都能用、能 `this.`(对象的长期家当)
- **局部变量/参数**:写在「方法那层大括号」里 → 只在那个方法活,出大括号就没、不能 `this.`(临时工)

### `extends` vs `implements`
- 类继承类 / 接口继承接口 → `extends`
- 类实现接口 → `implements`
- 一个类只能 extends 一个父类,但能 implements 多个接口。

### 接口 vs 抽象类
- **接口**:偏"能干啥的契约"。方法默认只声明不实现(Java8+ 可用 `default` 写带方法体的统一实现)。
- **抽象类**:偏"半成品骨架",能同时放「统一方法」+「abstract 留空给子类填的方法」。
  - 「固定流程 + 几步可变」用抽象类 = **模板方法模式**。
- 小项目里 Service 写不写接口都行,别被"必须有接口"绕晕。

### 构造器
对象一被 new 出来就自动执行的代码。语法:`类型 变量 = new 类名(参数);`
- 不写构造器 → Java 默认送你一个**无参构造器**。
- 一旦写了带参的,默认无参就没了,要用得自己补 `public 类(){}`。

---

## 三、Spring 核心

### bean / 依赖注入(DI)
- **bean** = 被 Spring 创建并托管的对象。
- 类上有 `@Service`/`@RestController`/`@Component`/`@Mapper` 等 → Spring 启动时自动造成 bean、注入好依赖。
- **不要自己 `new`** 带这些注解的类(自己 new 的没注入依赖,会空指针)。要用就**注入**。

### 构造器注入(三件套)
```java
private final ScheduleService scheduleService;            // ① 字段:存依赖
public ScheduleController(ScheduleService scheduleService){ // ② 构造器:Spring 递依赖的门
    this.scheduleService = scheduleService;              // ③ 把递进来的存进字段
}
```
为什么:Spring 把造好的 service bean 通过构造器参数塞进来,这样 `save()` 才不空指针。

### 常见注解
| 注解 | 作用 |
|---|---|
| `@RestController` | 这是个控制器,方法返回值自动转 JSON |
| `@RequestMapping("/api/schedule")` | 类上的基础路径 |
| `@PostMapping("/send")` / `@GetMapping` | 方法上的子路径 + HTTP 方式 |
| `@RequestParam("file")` | 把请求里名叫 file 的字段塞进参数 |
| `@Service` | 业务层 bean |
| `@Mapper` | MyBatis 自动生成这个接口的实现 |

### 接口地址 = 前后端契约
完整地址 = `@RequestMapping` + `@PostMapping` 拼起来,如 `/api/schedule/send`。
前端调时**路径、HTTP方式、参数名**三样都要对齐,否则 404/405/400。
接口方法**不用自己调**,前端一发对应请求,Spring 自动调。

---

## 四、MyBatis-Plus

### Mapper(数据访问层)
```java
@Mapper
public interface ScheduleItemMapper extends BaseMapper<ScheduleItemEntity> {}
```
继承 `BaseMapper` 后白嫖一整套 CRUD:`insert`/`selectById`/`updateById`/`deleteById`/`selectList`。
接口是空的,因为 SQL 由 MyBatis-Plus 根据实体的 `@Table`/`@Column` 自动生成。

### Service 用 Mapper
```java
public class ScheduleService extends ServiceImpl<ScheduleItemMapper, ScheduleItemEntity>
```
ServiceImpl 内部有个 `baseMapper` 字段(=你指定的 Mapper 的 bean,Spring 注入)。
所以 `this.save()`/`this.saveBatch()` 底层就是调 `baseMapper.insert(...)` 存库。
→ 这也是为什么 Service 必须是 Spring bean:`baseMapper` 靠注入,自己 new 的话它是 null。

---

## 五、文件上传 / 其它

### MultipartFile
接收上传文件的类型。前端发 `multipart/form-data`,后端用它接。
常用方法:`getBytes()`(取字节)、`getOriginalFilename()`(原文件名)、`getContentType()`(MIME类型)。
- 单文件:`@RequestParam("file") MultipartFile file`
- 多文件:`MultipartFile[]` 或 `List<MultipartFile>`

### getContentType() 返回啥
文件的 MIME 类型字符串,如 `"image/png"`、`"image/jpeg"`、`"application/pdf"`,判断不出来返回 `null`。
校验图片:`!contentType.startsWith("image/")` 就拦掉。

### Java 常用数据结构
| 要啥 | 用啥 | 常用实现 |
|---|---|---|
| 有序、可重复、有下标 | List | ArrayList |
| 去重 | Set | HashSet |
| key 查 value | Map | HashMap |
| 排队/先进先出 | Queue | ArrayDeque |
| 定长、最底层 | 数组 | int[] / String[] |
先把 ArrayList / HashMap / HashSet 用熟,日常 90% 够用。

---

## 待补充
（以后问到的新语法继续往这加）
