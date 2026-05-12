package org.example.academicagentbackend.common;

import lombok.Data;
//用来处理后端返回数据的
@Data
public class Result<T> {
    private Integer code;    // 状态码：比如 200 是成功，400 是参数错误
    private String message;  // 提示信息：比如 "上传成功" 或 "文件不存在"
    private T data;          // 实际的数据：可以是对象、列表，或者图片的 URL

    // 快捷成功的静态方法
    public static <T> Result<T> success(T data) {
        Result<T> result = new Result<>();
        result.setCode(200);
        result.setMessage("操作成功");
        result.setData(data);
        return result;
    }

    // 快捷失败的静态方法
    public static <T> Result<T> error(Integer code, String message) {
        Result<T> result = new Result<>();
        result.setCode(code);
        result.setMessage(message);
        return result;
    }
}