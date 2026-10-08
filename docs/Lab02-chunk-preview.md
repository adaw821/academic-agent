# Lab02 Basic SQL.pptx 分片预览

仅离线解析与分片，未写入数据库、未生成向量。

```json
{
  "slides": 18,
  "old_parent_count": 3,
  "old_child_counts": {
    "150": 55,
    "300": 26
  },
  "new_parent_count": 14,
  "new_child_count": 14
}
```

## 第 1 页

子片数：1；图片占位符：0。

### 子片 1（47 字符）

````markdown
# Lab 2 Basic SQL

United International College
````

## 第 2 页

子片数：1；图片占位符：0。

### 子片 1（50 字符）

````markdown
# Outline

- Basic Query

- Predicate

- Exercises
````

## 第 3 页

子片数：1；图片占位符：0。

### 子片 1（525 字符）

````markdown
# Introduction

- IBM Sequel Language was developed as a part of System R project to manage data in a relational database in the early 1970’s.

- Later the language was renamed as Structured Query Language, aka SQL (reads “S-Q-L” or “sequel”).

- SQL can define and manipulate data.

- Data definition is used when a user wants to create new databases or tables.

- Data manipulation or querying allows users search information from the database.

- The labs start from querying. Data definitions will be introduced in Lab 6.
````

## 第 4 页

子片数：1；图片占位符：0。

### 子片 1（367 字符）

````markdown
# Query

- A query is a question asked by database users to retrieve some data.

- For example, “what are the students whose GPA is greater than 3.0?”

- Database systems search for the students who satisfy the condition.

- The query language cannot change data in databases. Only data definition language can.

- Thus, the answers to queries only temporarily exist.
````

## 第 5 页

子片数：1；图片占位符：1。

### 子片 1（284 字符）

````markdown
# Basic Query

- Execute XAMPP and launch Apache and MySQL servers.

- Open phpMyAdmin.

- (Suppose the database “sakila” is already imported.) Select “sakila” from the database list.

- Click “SQL” from the menu bar.

- Type in the query “SELECT * FROM actor WHERE 1” and click “Go”.
````

## 第 6 页

子片数：1；图片占位符：1。

### 子片 1（150 字符）

````markdown
# Basic Query

- A part of the answer is like this.

- Same as selecting the table “actor” from the database list (what we have done in the last lab.)
````

## 第 7 页

子片数：1；图片占位符：0。

### 子片 1（771 字符）

````markdown
# Basic Query

- The basic query has three clauses: SELECT, FROM, and WHERE.

- SELECT: contains one or multiple attributes.

- These attributes are displayed in the result.

- The symbol “*” means all attributes.

- The FROM clause contains one or more tables.

- This lab handles the one table case.

- The WHERE clause contains a single predicate.

- It is a logical test on every row of the table which returns true or false.

- If multiple queries are executed at the same time, a semicolon “;” is used as a delimiter to split two queries.

- A query is executed as follows.

- The system test the predicate on every tuple from the table in the FROM clause.

- If a tuple satisfies the predicate, show the values of the attributes in the SELECT clause in the result.
````

## 第 8 页

子片数：0；图片占位符：0。

无可检索正文，跳过。解析内容：

# Basic Query

## 第 9 页

子片数：1；图片占位符：0。

### 子片 1（208 字符）

````markdown
# Basic Query

- Let’s see another example.

SELECT title,release\_year FROM film WHERE rental\_rate&lt;1

- Please try to tell the meaning of this query.

- And execute it in the system to check the outcome.
````

## 第 10 页

子片数：1；图片占位符：0。

### 子片 1（455 字符）

````markdown
# Predicate

- The predicate in the WEHER clause is regarded as a logic test. The return value of a predicate is a Boolean, either 1 (True) or 0 (False).

- Formally,

- suppose op is a relation operator, which can be =, &gt;, &gt;=, &lt;, &lt;=, and &lt;&gt; (not equal);

- a predicate can be a single term

pred = term

term = exp op exp

“exp” is an arithmetic expression which contains attributes and constants.

- For example,

- rental\_rate &lt; 1
````

## 第 11 页

子片数：1；图片占位符：0。

### 子片 1（397 字符）

````markdown
# Predicate

- A predicate can also be a composition of terms.

pred = NOT pred

pred = pred AND pred

pred = pred OR pred

- NOT, AND, OR are logical operators.

- For example,

- NOT rental\_rate &lt; 1

- (NOT rental\_rate &lt; 1) AND release\_year = 2006

- Sometimes brackets are used to indicate the precedence.

- If the WHERE clause does not return a Boolean, the query has a syntax error.
````

## 第 12 页

子片数：0；图片占位符：0。

无可检索正文，跳过。解析内容：

# Predicate

## 第 13 页

子片数：1；图片占位符：0。

### 子片 1（228 字符）

````markdown
# String constant

- The constant in the comparison can be a string, but it has to be quoted in the quotation marks.

- For example,

SELECT * FROM film WHERE rating="PG-13"

- Try this example and tell the meaning of the query.
````

## 第 14 页

子片数：1；图片占位符：0。

### 子片 1（333 字符）

````markdown
# Case insensitive

- SQL is case insensitive.

- For example,

SeLeCt * From Film wHERE rAtIng="pg-13"

gives the same answer as the previous example.

- However, to make query readable, we write

- key words (like SELECT, FROM, etc.) in capital;

- attributes and tables in lower cases; and

- string constant in the original form.
````

## 第 15 页

子片数：0；图片占位符：0。

无可检索正文，跳过。解析内容：

# Basic Query

## 第 16 页

子片数：1；图片占位符：0。

### 子片 1（479 字符）

````markdown
# Example

- To write a good query, you need to pay attention to three things.

1. Which table is used?

2. What is the predicate?

3. What are the attributes in the result?

- For example, “Find the category ID for Sci-Fi movies”.

4. Table: category

5. Predicate: Sci-Fi movies

6. Attribute: category ID

- Thus, the query is

SELECT category\_id FROM category WHERE name="Sci-Fi"

- For this example, you need to know “Sci-Fi movies” is the attribute “name” of the category.
````

## 第 17 页

子片数：1；图片占位符：0。

### 子片 1（737 字符）

````markdown
# Exercises

Write SQLs for the following questions.

1. Find the information of actors whose first name is Russell.

2. Find the information of actors whose first name is Russell and last name is Close.

3. Find the email of customers whose first name is Harry and active is 0.

4. Find the full name of the actor whose id is 99.

5. Find the title and special features of the films whose replacement cost is lower than 20 and rental rate is higher than 4.0.

6. Find the customer id of the customers who have made a payment on 2005-05-25 but the amount does not exceed 3.

Save your queries in a txt file. Rename it as “COMP3013 Lab2 ###.txt”, where “###” is your student ID. And submit it on iSpace. The DDL is 24 hours after the lab.
````

## 第 18 页

子片数：0；图片占位符：0。

无可检索正文，跳过。解析内容：

# End of Lab 2
