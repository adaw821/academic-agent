package org.example.academicagentbackend.model;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

//课程表的实体类！！！！

@Entity
@Table(name = "schedule_item")
@Data  // Lombok自动生成getter/setter
@NoArgsConstructor  // 无参构造器
@AllArgsConstructor // 全参构造器

public class ScheduleItemEntity {
    @Id
    @GeneratedValue(strategy= GenerationType.IDENTITY)
    private int id;

    @Column(name = "user_id")
    private Long userId;

    @Column(name = "course_name")
    private String courseName;

    @Column(name = "course_id")
    private String course_id;

    @Column(name = "day_of_week")
    private int day_of_week;

    @Column(name = "start_time")
    private String start_time;

    @Column(name = "end_time")
    private String end_time;

    @Column(name = "location")
    private String location;

}
