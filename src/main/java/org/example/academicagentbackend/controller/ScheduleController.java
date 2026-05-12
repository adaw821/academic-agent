package org.example.academicagentbackend.controller;

import org.example.academicagentbackend.model.CourseInfoEntity;
import org.example.academicagentbackend.model.ScheduleItemEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import javax.xml.transform.Result;

//后端只返回数据 生成课表前端做
@RestController
//基础路径！！！一眼看到嗯
@RequestMapping("/schedule")

public class ScheduleController {
    //前端点击接口发送一张图片给后端
    //后端给llm解析后会返回json数据
    //然后前端把json包装成内置课表
    @PostMapping(value = "/send")
    public Result<ScheduleItemEntity> handleScheduleImage(MultipartFile file){

    }
}
